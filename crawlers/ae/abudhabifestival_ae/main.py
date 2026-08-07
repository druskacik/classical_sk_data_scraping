import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.abudhabifestival.ae/'
PROGRAMME_URL = urljoin(SOURCE_URL, 'programme-tickets')
SOURCE = 'Abu Dhabi Festival'
CITY = 'Abu Dhabi'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# Webflow gives the upcoming and retained past-event CMS collections separate
# pagination parameters.  Their order in the document is stable: upcoming first.
COLLECTIONS = (('d4a20d74', 0), ('f04b33c5', 1))

LOCATION_MARKERS = (
    ('Abu Dhabi', 'Abu Dhabi', 'AE'),
    ('Dubai', 'Dubai', 'AE'),
    ('New York', 'New York', 'US'),
    ('Carnegie Hall', 'New York', 'US'),
    ('Symphony Space', 'New York', 'US'),
    ('NYU Skirball', 'New York', 'US'),
    ('Metropolitan Opera House', 'New York', 'US'),
    ('Washington, D.C.', 'Washington, D.C.', 'US'),
    ('Redlands Bowl', 'Redlands', 'US'),
    ('Walt Disney Concert Hall', 'Los Angeles', 'US'),
    ('Vienna', 'Vienna', 'AT'),
    ('Schönbrunn', 'Vienna', 'AT'),
    ('Seoul', 'Seoul', 'KR'),
    ('Museo Universidad de Navarra', 'Pamplona', 'ES'),
    ('Teatro Romano de Mérida', 'Mérida', 'ES'),
    ('Beirut', 'Beirut', 'LB'),
    ('Opéra de Lyon', 'Lyon', 'FR'),
    ('Lyon', 'Lyon', 'FR'),
    ('Grande Halle de la Villette', 'Paris', 'FR'),
    ('Opera National de Paris', 'Paris', 'FR'),
    ('Opéra National de Paris', 'Paris', 'FR'),
    ('Kurhaus Wiesbaden', 'Wiesbaden', 'DE'),
    ('Kensington Palace', 'London', 'GB'),
    ('London', 'London', 'GB'),
)


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '').replace('\u200d', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session


def fetch_html(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.text


def parse_date(value):
    value = clean_text(value)
    try:
        return datetime.strptime(value, '%B %d, %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2})(?::|\.)(\d{2})\s*([AP]M)\b', clean_text(value), re.I)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not 1 <= hour <= 12 or minute > 59:
        return None
    if match.group(3).upper() == 'PM' and hour != 12:
        hour += 12
    elif match.group(3).upper() == 'AM' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def resolve_location(venue):
    venue = clean_text(venue)
    if not venue or venue.casefold() == 'global':
        return None

    for marker, city, country_code in LOCATION_MARKERS:
        if marker.casefold() in venue.casefold():
            # A city/country label is not a venue. Detail pages use the same
            # value, so these records cannot be made valid and are skipped.
            bare_location = re.fullmatch(
                rf'{re.escape(city)}\s*(?:,\s*(?:United Arab Emirates|UAE|USA|Lebanon))?',
                venue,
                re.I,
            )
            if bare_location:
                return None
            return venue, city, country_code

    # Unqualified venues in the Abu Dhabi Stage are the festival's home venues.
    return venue, CITY, 'AE'


def parse_listing_item(item):
    link = item.select_one('a[href^="/events/"]')
    title_node = item.select_one('.programme-event-title')
    date_node = item.select_one('[event-start-date]')
    venue_node = item.select_one('[fs-list-field="venue"]')
    if not link or not title_node or not date_node or not venue_node:
        return None

    title = clean_text(title_node)
    event_date = parse_date(date_node)
    location = resolve_location(venue_node)
    href = clean_text(link.get('href'))
    if not title or not event_date or not href or not location:
        return None

    time_node = item.select_one('.programme-time')
    venue, city, country_code = location
    return {
        'title': title,
        'date': event_date,
        'url': urljoin(SOURCE_URL, href),
        'time_from': parse_time(time_node) if time_node else None,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def listing_records(session):
    records = {}
    for parameter, collection_index in COLLECTIONS:
        # An empty page is a more reliable terminator than the progressively
        # enhanced Webflow next link, and the cap guards against site regressions.
        for page in range(1, 101):
            url = f'{PROGRAMME_URL}?{parameter}_page={page}'
            soup = BeautifulSoup(fetch_html(session, url), 'html.parser')
            collections = soup.select('.programme-filter-collection-wrapper.w-dyn-list')
            if collection_index >= len(collections):
                break
            items = collections[collection_index].select('.programme.w-dyn-item')
            if not items:
                break
            for item in items:
                record = parse_listing_item(item)
                if record:
                    records[record['url']] = record
    return list(records.values())


def detail_description(html):
    soup = BeautifulSoup(html, 'html.parser')
    content = soup.select_one('.programme-rich-text')
    return clean_text(content) or None


def get_concerts():
    session = make_session()
    records = listing_records(session)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_html, session, record['url']): record for record in records}
        for future in as_completed(futures):
            record = futures[future]
            try:
                record['description'] = detail_description(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Abu Dhabi Festival event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=record['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(records, key=lambda record: (
        record['date'], record['time_from'] or '', record['title'], record['url']
    ))


class AbuDhabiFestivalAeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='abudhabifestival_ae',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AE',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from'],
    )

    def scrape(self):
        return get_concerts()


def main():
    AbuDhabiFestivalAeCrawler().run()


if __name__ == '__main__':
    main()
