import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://lpo.org.uk/'
SOURCE = 'London Philharmonic Orchestra'
EVENTS_API_URL = f'{SOURCE_URL}wp-json/wp/v2/Event'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# Some touring pages identify the location rather than spelling out the hall.
# These are the venue-specific LPO series represented by those labels.
LOCATION_DEFAULTS = {
    'Brighton': ('Brighton Dome', 'Brighton'),
    'Eastbourne': ('Congress Theatre', 'Eastbourne'),
    'Glyndebourne': ('Glyndebourne', 'Lewes'),
    'Saffron Walden': ('Saffron Hall', 'Saffron Walden'),
}

VENUE_CITIES = {
    'Barbican': 'London',
    'Congress Theatre': 'Eastbourne',
    'Glyndebourne': 'Lewes',
    'Queen Elizabeth Hall': 'London',
    'Royal Albert Hall': 'London',
    'Royal Festival Hall': 'London',
    'Saffron Hall': 'Saffron Walden',
}

DATE_RE = re.compile(
    r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) '
    r'\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) '
    r'\d{4}(?:\s*,\s*\d{1,2}\.\d{2}(?:am|pm))?$',
    re.I,
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url, **kwargs):
    response = session.get(url, timeout=45, **kwargs)
    response.raise_for_status()
    return response


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session


def event_urls(session):
    """Return every published event exposed by the site's public REST API."""
    urls = set()
    page = 1
    while True:
        response = get_response(
            session,
            EVENTS_API_URL,
            params={
                'per_page': 100,
                'page': page,
                'orderby': 'id',
                'order': 'asc',
                '_fields': 'link',
            },
        )
        items = response.json()
        urls.update(item['link'] for item in items if item.get('link'))
        if page >= int(response.headers.get('X-WP-TotalPages', '1')):
            break
        page += 1
    return urls


def parse_start(value):
    value = re.sub(r'\s+', ' ', clean_text(value)).strip()
    value = re.sub(r'\s*,\s*', ', ', value)
    if not DATE_RE.fullmatch(value):
        return None
    for pattern in ('%a %d %b %Y, %I.%M%p', '%a %d %b %Y'):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def resolve_location(value):
    value = clean_text(value)
    if value in LOCATION_DEFAULTS:
        return LOCATION_DEFAULTS[value]

    if ',' in value:
        city, venue = (part.strip() for part in value.split(',', 1))
        if city and venue:
            return venue, city

    for venue, city in VENUE_CITIES.items():
        if venue.casefold() in value.casefold():
            return value, city
    return None


def description(soup):
    # The intro block contains the full programme followed by the event prose.
    # It intentionally retains performers too: programme labels and role names
    # help the downstream programme extractor understand operatic events.
    intro = soup.select_one('main .intro-block')
    return clean_text(intro) or None


def detail_records(session, url):
    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
    title = clean_text(soup.select_one('main h1') or soup.find('h1'))
    details = soup.select_one('main .event-details')
    if not title or not details:
        return []

    values = [clean_text(node) for node in details.select('p.medium')]
    starts = [start for value in values if (start := parse_start(value))]
    location_text = next(
        (value for value in reversed(values) if value and not parse_start(value)),
        '',
    )
    location = resolve_location(location_text)
    if not starts or not location:
        return []

    venue, city = location
    body = description(soup)
    return [
        {
            'title': title,
            'date': start.date().isoformat(),
            'url': url,
            'time_from': start.strftime('%H:%M') if start.hour or start.minute else None,
            'venue': venue,
            'city': city,
            'country_code': 'GB',
            'description': body,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for start in starts
    ]


def get_concerts():
    session = make_session()
    urls = event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(detail_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape LPO event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class LpoOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='lpo_org_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    LpoOrgUkCrawler().run()


if __name__ == '__main__':
    main()
