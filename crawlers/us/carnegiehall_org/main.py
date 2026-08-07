import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.carnegiehall.org/'
EVENTS_URL = urljoin(SOURCE_URL, 'events')
SOURCE = 'Carnegie Hall'
ALGOLIA_URL = 'https://q0tmlopf1j-2.algolianet.com/1/indexes/prod_Events/query'
ALGOLIA_APP_ID = 'Q0TMLOPF1J'
ALGOLIA_API_KEY = 'd2d2b382f2659c44ef8927aad7a24172'
HITS_PER_PAGE = 1000

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'X-Algolia-Application-Id': ALGOLIA_APP_ID,
    'X-Algolia-API-Key': ALGOLIA_API_KEY,
}

# Offsite concerts normally identify their destination in the title.  Values
# are ISO 3166-1 alpha-2 codes; aliases reflect wording used by the calendar.
COUNTRIES = {
    'austria': 'AT', 'belgium': 'BE', 'canada': 'CA', 'china': 'CN',
    'england': 'GB', 'finland': 'FI', 'france': 'FR', 'germany': 'DE',
    'italy': 'IT', 'japan': 'JP', 'netherlands': 'NL', 'scotland': 'GB',
    'south korea': 'KR', 'spain': 'ES', 'switzerland': 'CH', 'taiwan': 'TW',
    'united kingdom': 'GB', 'united states': 'US', 'usa': 'US',
}
NYC_OFFSITE_VENUES = {
    'Bryant Park', 'Central Park', 'Flushing Town Hall', 'Governors Island',
    'Hudson Yards', 'Madison Square Park', 'National Sawdust',
    'The Metropolitan Museum of Art', 'Times Square',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(unescape(str(value)), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r' *\n+ *', '\n', text).strip()


def parse_time(value):
    match = re.fullmatch(r'\s*(\d{1,2})(?::(\d{2}))?\s*([AP])M\s*', value or '', re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12 + (12 if match.group(3).upper() == 'P' else 0)
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def offsite_location(hit):
    venue = clean_text(hit.get('facility'))
    if venue in NYC_OFFSITE_VENUES:
        return 'New York', 'US'

    title = clean_text(hit.get('title'))
    match = re.search(r'\bin\s+([^,\n]+),\s*([^,\n]+)\s*$', title, re.I)
    if not match:
        return None
    city = match.group(1).strip()
    country_name = match.group(2).strip().lower()
    country_code = COUNTRIES.get(country_name)
    if not city or not country_code:
        return None
    return city, country_code


def description_from_hit(hit):
    parts = []
    for field in ('subtitle', 'webdisplayperformers'):
        value = clean_text(hit.get(field))
        if value and value not in parts:
            parts.append(value)
    repertoire = hit.get('repertoire') or []
    if isinstance(repertoire, str):
        repertoire = [repertoire]
    works = [clean_text(item) for item in repertoire if clean_text(item)]
    if works:
        parts.append('Program\n' + '\n'.join(works))
    return '\n\n'.join(parts) or None


def record_from_hit(hit):
    title = clean_text(hit.get('title'))
    venue = clean_text(hit.get('facility'))
    path = hit.get('url')
    date_text = hit.get('date')
    if not title or not venue or not path or not date_text:
        return None
    try:
        # startdate is an instant and therefore rolls evening NYC concerts into
        # the next UTC day.  Algolia's display date is the event's local date.
        event_date = datetime.strptime(date_text, '%A, %b %d, %Y').date().isoformat()
    except ValueError:
        return None

    if hit.get('facilityfacet') == 'Offsite':
        location = offsite_location(hit)
        if not location:
            return None
        city, country_code = location
    else:
        city, country_code = 'New York', 'US'

    return {
        'title': title,
        'date': event_date,
        'url': urljoin(SOURCE_URL, path),
        'time_from': parse_time(hit.get('time')),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description_from_hit(hit),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def fetch_page(session, page, year):
    start = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    response = session.post(
        ALGOLIA_URL,
        json={
            'query': '',
            'hitsPerPage': HITS_PER_PAGE,
            'page': page,
            'numericFilters': [f'startdate>={start}', f'startdate<{end}'],
            'attributesToHighlight': [],
            'attributesToSnippet': [],
        },
        timeout=45,
    )
    response.raise_for_status()
    return response.json()


def scrape_concerts(session=None):
    session = session or requests.Session()
    session.headers.update(HEADERS)
    records = []
    # The index's oldest result is from 2020. Year windows avoid Algolia's
    # 1,000-result pagination ceiling while retaining its full available archive.
    for year in range(2020, datetime.now(timezone.utc).year + 4):
        page = 0
        while True:
            try:
                payload = fetch_page(session, page, year)
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to fetch Carnegie Hall event index',
                    event='crawler_page_failed',
                    level='warning',
                    url=ALGOLIA_URL,
                    year=year,
                    page=page,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                if not records:
                    raise
                break

            hits = payload.get('hits') or []
            for hit in hits:
                record = record_from_hit(hit)
                if record:
                    records.append(record)

            page += 1
            if not hits or page >= int(payload.get('nbPages') or 0):
                break

    unique = {
        (item['title'], item['date'], item['time_from'], item['venue'], item['url']): item
        for item in records
    }
    result = sorted(unique.values(), key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))
    if not result:
        log_message(
            'No valid Carnegie Hall events found',
            event='crawler_empty_listing',
            level='warning',
            url=EVENTS_URL,
            record_count=0,
        )
    return result


class CarnegieHallOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='carnegiehall_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'url'],
    )

    def scrape(self):
        return scrape_concerts()


def main():
    CarnegieHallOrgCrawler().run()


if __name__ == '__main__':
    main()
