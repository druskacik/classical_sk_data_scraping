import re
from datetime import datetime
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.rpo.co.uk/'
SOURCE = 'Royal Philharmonic Orchestra'
ALGOLIA_URL = 'https://iqfdbc26qj-dsn.algolia.net/1/indexes/SEARCH_production_ranked/query'
ALGOLIA_HEADERS = {
    'X-Algolia-Application-Id': 'IQFDBC26QJ',
    'X-Algolia-API-Key': '27e7b4c70ca9f3b5cc1e97a3ec2d8ecd',
    'User-Agent': 'Mozilla/5.0 (compatible; classical-concert-crawler/1.0)',
}

# The site's international hierarchy identifies the country but not the city.
# These tokens cover the touring venues and city names used in its index. An
# unrecognised international event is skipped instead of receiving a guess.
INTERNATIONAL_COUNTRIES = {
    'Germany': 'DE',
    'Ireland': 'IE',
    'Italy': 'IT',
    'Japan': 'JP',
    'Switzerland': 'CH',
    'United States': 'US',
}
CITY_TOKENS = {
    'dublin': 'Dublin',
    'hamburg': 'Hamburg',
    'lucerne': 'Lucerne',
    'luzern': 'Lucerne',
    'zurich': 'Zurich',
    'zürich': 'Zurich',
    'geneva': 'Geneva',
    'geneve': 'Geneva',
    'gstaad': 'Gstaad',
    'munich': 'Munich',
    'münchen': 'Munich',
    'cologne': 'Cologne',
    'köln': 'Cologne',
    'frankfurt': 'Frankfurt',
    'stuttgart': 'Stuttgart',
    'berlin': 'Berlin',
    'düsseldorf': 'Dusseldorf',
    'dusseldorf': 'Dusseldorf',
    'tokyo': 'Tokyo',
    'osaka': 'Osaka',
    'kyoto': 'Kyoto',
    'nagoya': 'Nagoya',
    'yokohama': 'Yokohama',
    'fukuoka': 'Fukuoka',
    'sapporo': 'Sapporo',
    'tachikawa': 'Tachikawa',
    'niigata': 'Niigata',
    'rome': 'Rome',
    'roma': 'Rome',
    'ravenna': 'Ravenna',
    'turin': 'Turin',
    'new york': 'New York',
    'dreyfoos': 'West Palm Beach',
}


def clean_text(value):
    if not value:
        return ''
    value = str(value)
    text = (
        BeautifulSoup(value, 'html.parser').get_text('\n', strip=True)
        if '<' in value and '>' in value
        else value
    )
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def query_index(session, start_timestamp, end_timestamp):
    params = urlencode(
        {
            'facetFilters': '["include_in_listing:true","section_handle:Events"]',
            'numericFilters': (
                f'["event_start_date_timestamp>={start_timestamp}",'
                f'"event_start_date_timestamp<{end_timestamp}"]'
            ),
            'hitsPerPage': 1000,
            'page': 0,
            'query': '',
        }
    )
    response = session.post(ALGOLIA_URL, json={'params': params}, timeout=60)
    response.raise_for_status()
    return response.json()


def fetch_range(session, start_timestamp, end_timestamp):
    payload = query_index(session, start_timestamp, end_timestamp)
    hit_count = payload.get('nbHits', 0)
    if hit_count <= 1000:
        return payload.get('hits') or []

    midpoint = (start_timestamp + end_timestamp) // 2
    if midpoint <= start_timestamp:
        log_message(
            'Algolia result window cannot be divided further',
            event='crawler_result_limit',
            level='warning',
            record_count=hit_count,
        )
        return payload.get('hits') or []
    return fetch_range(session, start_timestamp, midpoint) + fetch_range(
        session, midpoint, end_timestamp
    )


def resolve_location(event):
    location = event.get('location') or {}
    level_zero = location.get('lvl0') or []
    level_one = location.get('lvl1') or []
    if 'UK' in level_zero:
        for value in level_one:
            if value.startswith('UK > '):
                city = clean_text(value.split(' > ', 1)[1])
                return (city, 'GB') if city else (None, None)

    country_name = None
    for value in level_one:
        if value.startswith('International > '):
            country_name = clean_text(value.split(' > ', 1)[1])
            break
    country_code = INTERNATIONAL_COUNTRIES.get(country_name)
    searchable = f"{event.get('venue', '')} {event.get('title', '')}".lower()
    if country_code:
        for token, city in CITY_TOKENS.items():
            if token in searchable:
                return city, country_code
    return None, None


def make_description(event):
    parts = []
    description = clean_text(event.get('description'))
    repertoire = clean_text(event.get('repertoire'))
    if description:
        parts.append(description)
    if repertoire and repertoire.lower() not in description.lower():
        parts.append(f'Programme\n{repertoire}')
    return '\n\n'.join(parts) or None


def make_record(event):
    title = clean_text(event.get('title'))
    url = clean_text(event.get('url'))
    venue = clean_text(event.get('venue'))
    start = clean_text(event.get('event_start_date'))
    city, country_code = resolve_location(event)
    if not title or not url or not venue or not city or not country_code or not start:
        return None
    try:
        parsed = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return {
        'title': title,
        'date': parsed.date().isoformat(),
        'url': url,
        'time_from': parsed.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': make_description(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(ALGOLIA_HEADERS)
    # Recursive windows avoid Algolia's 1,000-result pagination limit while
    # retaining the site's published archive as well as future performances.
    events = fetch_range(session, 0, 4102444800)  # 1970-01-01 through 2100-01-01
    records = [record for event in events if (record := make_record(event))]
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class RpoCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='rpo_co_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='potential',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'venue',
            'city',
            'country_code',
            'description',
            'source_url',
            'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    RpoCoUkCrawler().run()


if __name__ == '__main__':
    main()
