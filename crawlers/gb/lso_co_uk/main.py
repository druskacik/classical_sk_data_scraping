import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.lso.co.uk/'
SOURCE = 'London Symphony Orchestra'
SITEMAP_URL = f'{SOURCE_URL}sitemap-posttype-event.xml'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

MONTHS = (
    'January|February|March|April|May|June|July|August|September|October|'
    'November|December'
)
DATE_RE = re.compile(rf'\b\w+\s+(\d{{1,2}})\s+({MONTHS})(?:\s+(20\d{{2}}))?', re.I)
TIME_RE = re.compile(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', re.I)

# The calendar includes international LSO tours. These are event cities seen in
# the source's tour archive; London remains the defensible default only for
# explicitly named London venues.
CITY_COUNTRIES = {
    'Aldeburgh': 'GB', 'Basingstoke': 'GB', 'Bristol': 'GB', 'London': 'GB',
    'Snape': 'GB', 'Beijing': 'CN', 'Shanghai': 'CN', 'Tokyo': 'JP',
    'Osaka': 'JP', 'Seoul': 'KR', 'Taipei': 'TW', 'Hong Kong': 'HK',
    'Berlin': 'DE', 'Bonn': 'DE', 'Cologne': 'DE', 'Dortmund': 'DE',
    'Dresden': 'DE', 'Essen': 'DE', 'Frankfurt': 'DE', 'Hamburg': 'DE',
    'Munich': 'DE', 'Baden-Baden': 'DE', 'Paris': 'FR', 'Lyon': 'FR',
    'Amsterdam': 'NL', 'Brussels': 'BE', 'Luxembourg': 'LU',
    'Ljubljana': 'SI', 'Vienna': 'AT', 'Salzburg': 'AT', 'Prague': 'CZ',
    'Budapest': 'HU', 'Madrid': 'ES', 'Barcelona': 'ES', 'Lucerne': 'CH',
    'Zurich': 'CH', 'Milan': 'IT', 'Rome': 'IT', 'New York': 'US',
    'Boston': 'US', 'Los Angeles': 'US', 'San Francisco': 'US',
}

VENUE_LOCATIONS = {
    "LSO St Luke's": ('London', 'GB'),
    'Jerwood Hall': ('London', 'GB'),
    'Barbican': ('London', 'GB'),
    'Barbican Hall': ('London', 'GB'),
    'Royal Albert Hall': ('London', 'GB'),
    'Bristol Beacon': ('Bristol', 'GB'),
    'Snape Maltings Concert Hall': ('Snape', 'GB'),
    'National Centre for the Performing Arts': ('Beijing', 'CN'),
    'Beethoven Hall': ('Bonn', 'DE'),
    'Philharmonie Berlin': ('Berlin', 'DE'),
    'Philharmonie Essen': ('Essen', 'DE'),
    'Philharmonie de Paris': ('Paris', 'FR'),
    'Palau de la Música': ('Barcelona', 'ES'),
    'Cankarjev dom': ('Ljubljana', 'SI'),
    'Philharmonie Luxembourg': ('Luxembourg', 'LU'),
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount(
        'https://',
        HTTPAdapter(max_retries=Retry(
            total=2,
            backoff_factor=0.75,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=('GET',),
        )),
    )
    return session


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def event_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    return list(dict.fromkeys(
        clean_text(node)
        for node in soup.select('url > loc')
        if re.fullmatch(r'https://www\.lso\.co\.uk/whats-on/[^/]+/', clean_text(node))
    ))


def parse_dates(value):
    matches = list(DATE_RE.finditer(clean_text(value)))
    if not matches:
        return []
    final_year = next((m.group(3) for m in reversed(matches) if m.group(3)), None)
    dates = []
    for match in matches:
        year = match.group(3) or final_year
        if not year:
            continue
        try:
            parsed = datetime.strptime(
                f'{match.group(1)} {match.group(2)} {year}', '%d %B %Y'
            ).date()
        except ValueError:
            continue
        dates.append(parsed)

    if len(dates) == 2 and '—' in clean_text(value):
        start, end = dates
        if end >= start and (end - start).days <= 14:
            return [(start + timedelta(days=offset)).isoformat()
                    for offset in range((end - start).days + 1)]
    return list(dict.fromkeys(value.isoformat() for value in dates))


def parse_time(value):
    match = TIME_RE.search(clean_text(value))
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def event_description(soup):
    parts = []
    for selector in ('.c-event-details', '.c-event-description'):
        for node in soup.select(selector):
            text = clean_text(node)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def resolve_location(soup, title, description):
    venue = clean_text(soup.select_one('.c-event-masthead__venue'))
    pretitle = clean_text(soup.select_one('.c-event-masthead__pretitle'))
    evidence = '\n'.join((title, pretitle, description or ''))

    if not venue:
        for known_venue in VENUE_LOCATIONS:
            if known_venue.casefold() in evidence.casefold():
                venue = known_venue
                break
    if not venue and pretitle and not re.search(r'artist|concert|series', pretitle, re.I):
        venue = pretitle
    if not venue:
        return None

    for known_venue, location in VENUE_LOCATIONS.items():
        if known_venue.casefold() in venue.casefold():
            return venue, *location

    for city, country_code in CITY_COUNTRIES.items():
        if re.search(rf'\b{re.escape(city)}\b', venue, re.I):
            return venue, city, country_code
    for city, country_code in CITY_COUNTRIES.items():
        if re.search(rf'\b(?:on tour in|in)\s+{re.escape(city)}\b', title, re.I):
            return venue, city, country_code
    return None


def parse_event(content, url):
    soup = BeautifulSoup(content, 'html.parser')
    title = clean_text(soup.select_one('h1.c-masthead__title'))
    date_text = clean_text(soup.select_one('.c-event-masthead__date'))
    dates = parse_dates(date_text)
    description = event_description(soup)
    location = resolve_location(soup, title, description)
    if not title or not dates or not location:
        return []
    venue, city, country_code = location
    time_from = parse_time(date_text)
    return [{
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    } for event_date in dates]


def get_concerts():
    session = make_session()
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_event(future.result().content, url))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape LSO event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(records, key=lambda row: (
        row['date'], row['time_from'] or '', row['title'], row['venue']
    ))


class LsoCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='lso_co_uk',
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
    LsoCoUkCrawler().run()


if __name__ == '__main__':
    main()
