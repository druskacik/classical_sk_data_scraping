import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.nationalyouthchoir.org.uk/'
EVENTS_URL = urljoin(SOURCE_URL, 'events')
SOURCE = 'National Youth Choir'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def canonical_url(url):
    parts = urlsplit(url)
    path = re.sub(r'^/events/', '/Event/', parts.path, flags=re.IGNORECASE)
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_urls(session):
    soup = get_soup(session, EVENTS_URL)
    urls = {
        canonical_url(urljoin(SOURCE_URL, link['href']))
        for link in soup.select('a[href]')
        if re.match(r'^/events/[^/]+/?$', link.get('href', ''), re.IGNORECASE)
    }
    return sorted(urls)


def event_schema(soup):
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.string or node.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get('@type') == 'Event':
                return candidate
    return None


def local_start(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo('Europe/London'))
    return parsed


def make_record(url, soup):
    schema = event_schema(soup)
    if not schema:
        return None

    title = clean_text(schema.get('name'))
    start = local_start(schema.get('StartDate') or schema.get('startDate'))
    location = schema.get('Location') or schema.get('location') or {}
    address = location.get('Address') or location.get('address') or {}
    venue = clean_text(location.get('Name') or location.get('name'))
    city = clean_text(address.get('AddressRegion') or address.get('addressRegion'))
    country = clean_text(address.get('AddressCountry') or address.get('addressCountry')).upper()

    # AddressLocality contains the street on this platform; AddressRegion is
    # the actual town/city. Online-only recordings therefore remain invalid
    # and are deliberately skipped.
    if not title or not start or not venue or not city or country != 'GB':
        return None

    content = soup.select_one('.eventContent')
    description = clean_text(content) or clean_text(schema.get('Description')) or None
    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': canonical_url(url),
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    for url in listing_urls(session):
        try:
            record = make_record(url, get_soup(session, url))
        except requests.RequestException as error:
            log_message(
                'Failed to scrape concert detail',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        if record:
            records.append(record)
    return sorted(records, key=lambda item: (item['date'], item['time_from'], item['title']))


class NationalYouthChoirOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='nationalyouthchoir_org_uk',
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
    NationalYouthChoirOrgUkCrawler().run()


if __name__ == '__main__':
    main()
