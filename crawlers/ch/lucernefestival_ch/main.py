import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.lucernefestival.ch/de/'
SITEMAP_URL = 'https://www.lucernefestival.ch/sitemap.xml'
PROGRAMME_URL = 'https://www.lucernefestival.ch/de/karten/programm'
SOURCE = 'Lucerne Festival'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.7',
}
EVENT_URL_RE = re.compile(r'/de/programm/[^/]+/\d+/?$')
POSTAL_CITY_RE = re.compile(
    r'(?:(?P<prefix>CH|DE|D|AT|A|FR|F|IT|I)-?)?\s*'
    r'(?P<postal>\d{4,5})\s+(?P<city>[^,\n\r]+)',
    re.IGNORECASE,
)
COUNTRY_PREFIXES = {
    'CH': 'CH',
    'DE': 'DE',
    'D': 'DE',
    'AT': 'AT',
    'A': 'AT',
    'FR': 'FR',
    'F': 'FR',
    'IT': 'IT',
    'I': 'IT',
}


def clean_text(value):
    if value is None:
        return ''
    text = html.unescape(str(value))
    if '<' in text and '>' in text:
        text = BeautifulSoup(text, 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    session.mount('https://', HTTPAdapter(max_retries=retry))
    return session


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def event_urls_from_sitemap(session):
    response = get_response(session, SITEMAP_URL)
    root = ElementTree.fromstring(response.content)
    urls = {
        element.text.rstrip('/')
        for element in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
        if element.text and EVENT_URL_RE.search(element.text)
    }
    return sorted(urls)


def event_urls_from_listing(session):
    soup = BeautifulSoup(get_response(session, PROGRAMME_URL).text, 'html.parser')
    return sorted({
        urljoin(PROGRAMME_URL, link['href']).rstrip('/')
        for link in soup.select('a[href]')
        if EVENT_URL_RE.search(urljoin(PROGRAMME_URL, link['href']))
    })


def event_urls(session):
    try:
        urls = event_urls_from_sitemap(session)
        if urls:
            return urls
    except (requests.RequestException, ElementTree.ParseError) as error:
        log_message(
            'Failed to read Lucerne Festival sitemap; using programme listing',
            event='crawler_listing_fallback',
            level='warning',
            url=SITEMAP_URL,
            error_type=type(error).__name__,
            error_message=str(error),
        )
    return event_urls_from_listing(session)


def json_ld_event(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get('@type') == 'MusicEvent':
                return candidate
    return None


def resolve_location(event):
    location = event.get('location') or {}
    if not isinstance(location, dict):
        return None, None, None
    venue = clean_text(location.get('name'))
    address = clean_text(location.get('address'))
    if not venue:
        return None, None, None

    match = POSTAL_CITY_RE.search(address)
    if match:
        city = clean_text(match.group('city'))
        prefix = (match.group('prefix') or '').upper()
        country_code = COUNTRY_PREFIXES.get(prefix)
        if not country_code:
            country_code = 'CH' if len(match.group('postal')) == 4 else None
        if city and country_code:
            return venue, city, country_code

    # These names unambiguously identify Lucerne venues even when an event
    # page omits its postal address. Other venues are skipped to avoid
    # assigning the festival's home city to touring performances.
    venue_lower = venue.lower()
    if 'luzern' in venue_lower or 'lucerne' in venue_lower or venue_lower.startswith('kkl'):
        return venue, 'Luzern', 'CH'
    return None, None, None


def description_from_event(event):
    parts = []
    description = clean_text(event.get('description'))
    if description:
        parts.append(description)

    works = []
    for work in event.get('workPerformed') or []:
        name = clean_text(work.get('name') if isinstance(work, dict) else work)
        if name:
            works.append(name)
    if works:
        parts.append('Programm\n' + '\n'.join(works))
    return '\n\n'.join(parts) or None


def parse_event_page(url, content):
    soup = BeautifulSoup(content, 'html.parser')
    event = json_ld_event(soup)
    hidden = soup.select_one('#event-detail-data-hidden')
    if not event or not hidden:
        return None

    title = clean_text(hidden.get('data-title') or event.get('name'))
    start = hidden.get('data-date') or event.get('startDate') or ''
    try:
        start_at = datetime.fromisoformat(start)
        event_date = start_at.date().isoformat()
        time_from = start_at.strftime('%H:%M')
    except (TypeError, ValueError):
        return None

    venue, city, country_code = resolve_location(event)
    canonical_url = clean_text(event.get('url')) or url
    if not title or not canonical_url or not venue or not city or not country_code:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': canonical_url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description_from_event(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = make_session()
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_event_page(url, future.result().text)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Lucerne Festival event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class LucerneFestivalChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='lucernefestival_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
        # The festival also publishes talks, workshops, walks, and crossover
        # events, so every record must be classified before direct insertion.
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
    LucerneFestivalChCrawler().run()


if __name__ == '__main__':
    main()
