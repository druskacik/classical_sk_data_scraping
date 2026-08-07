import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://buehnenbern.ch/'
CONCERTS_URL = urljoin(SOURCE_URL, 'spielplan/konzerte/')
SOURCE = 'Bühnen Bern'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.7',
}

# Locations without an explicit municipality are all Bühnen Bern home venues.
BERN_VENUE_MARKERS = (
    'casino bern',
    'stadttheater',
    'vidmar',
    'bundesplatz',
    'berner münster',
    'zentrum paul klee',
    'progr',
    'kubus',
)


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response


def concert_catalog(html):
    soup = BeautifulSoup(html, 'html.parser')
    catalog = {}
    for item in soup.select('.cp-play-teaser-item'):
        link = item.select_one('a[href*="/spielplan/programm/"]')
        if not link:
            continue
        url = urljoin(SOURCE_URL, link.get('href', ''))
        path = urlparse(url).path.rstrip('/')
        if path == '/spielplan/programm' or not path.startswith('/spielplan/programm/'):
            continue
        info = clean_text(item.select_one('.added-info'))
        dates = list(dict.fromkeys(re.findall(r'\b\d{2}\.\d{2}\.\d{4}\b', info)))
        venue = info.split('\n', 1)[0].strip() if info else ''
        catalog[url] = {'dates': dates, 'venue': venue}
    return catalog


def parse_date(value):
    try:
        return datetime.strptime(value, '%d.%m.%Y').date().isoformat()
    except (TypeError, ValueError):
        return None


def city_for_venue(venue):
    normalized = venue.casefold()
    if 'biel' in normalized or 'bienne' in normalized:
        return 'Biel/Bienne'
    if 'langenthal' in normalized:
        return 'Langenthal'
    if 'thun' in normalized:
        return 'Thun'
    if 'solothurn' in normalized:
        return 'Solothurn'
    if 'zürich' in normalized or 'zurich' in normalized:
        return 'Zürich'
    if 'luzern' in normalized:
        return 'Luzern'
    if 'basel' in normalized:
        return 'Basel'
    if 'genève' in normalized or 'geneva' in normalized or 'genf' in normalized:
        return 'Genève'
    if 'lausanne' in normalized:
        return 'Lausanne'
    if 'bern' in normalized or any(marker in normalized for marker in BERN_VENUE_MARKERS):
        return 'Bern'
    return None


def event_description(soup):
    sections = []
    for selector in ('#intro .main-content', '#around .main-content'):
        text = clean_text(soup.select_one(selector))
        if text and text not in sections:
            sections.append(text)
    return '\n\n'.join(sections) or None


def make_records(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    title = clean_text(soup.select_one('.cp-play-title h1'))
    if not title:
        title = re.sub(r'\s*\|\s*Bühnen Bern\s*$', '', clean_text(soup.title))
    if not title:
        return []

    description = event_description(soup)
    records = []
    for item in soup.select('#calendar .cp-calendar-item'):
        event_date = parse_date(clean_text(item.select_one('.date')))
        time_from = clean_text(item.select_one('.time')) or None
        if time_from and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', time_from):
            time_from = None
        venue = clean_text(item.select_one('.location'))
        city = city_for_venue(venue)
        if not event_date or not venue or not city:
            continue
        records.append(
            {
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': 'CH',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )
    return records


def make_archive_records(url, html, fallback):
    """Use the explicit overview-card metadata after past calendar rows disappear."""
    soup = BeautifulSoup(html, 'html.parser')
    title = clean_text(soup.select_one('.cp-play-title h1'))
    venue = fallback['venue']
    city = city_for_venue(venue)
    if not title or not venue or not city:
        return []
    description = event_description(soup)
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': None,
            'venue': venue,
            'city': city,
            'country_code': 'CH',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for value in fallback['dates']
        if (event_date := parse_date(value))
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    catalog = concert_catalog(get_response(session, CONCERTS_URL).text)
    records = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_response, session, url): url for url in catalog}
        for future in as_completed(futures):
            url = futures[future]
            try:
                html = future.result().text
                detail_records = make_records(url, html)
                records.extend(
                    detail_records or make_archive_records(url, html, catalog[url])
                )
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Bühnen Bern concert',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    log_message(
        'Bühnen Bern concert archive parsed',
        event='crawler_scrape_completed',
        url=CONCERTS_URL,
        record_count=len(records),
    )
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class BuehnenBernChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='buehnenbern_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
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
    BuehnenBernChCrawler().run()


if __name__ == '__main__':
    main()
