import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.templemusic.org/'
CONCERTS_URL = urljoin(SOURCE_URL, 'concerts')
SOURCE = 'Temple Music Foundation'
CITY = 'London'

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
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def is_listing_url(url):
    path = urlparse(url).path.rstrip('/')
    return path == '/concerts' or path == '/concerts/church-events' or bool(
        re.fullmatch(r'/concerts/past-\d{4}', path)
    )


def discover_event_urls(session):
    # The current page links all available yearly archives. Keeping discovery
    # link-driven means newly published archive years are included automatically.
    current = get_soup(session, CONCERTS_URL)
    listing_urls = {CONCERTS_URL}
    for link in current.select('a[href]'):
        url = urljoin(CONCERTS_URL, link.get('href'))
        if is_listing_url(url):
            listing_urls.add(url)

    event_urls = set()
    for listing_url in sorted(listing_urls):
        soup = current if listing_url.rstrip('/') == CONCERTS_URL else get_soup(session, listing_url)
        for link in soup.select('a[href]'):
            url = urljoin(listing_url, link.get('href')).split('#', 1)[0]
            path = urlparse(url).path.rstrip('/')
            if (
                urlparse(url).netloc == urlparse(SOURCE_URL).netloc
                and path.startswith('/concerts/')
                and not is_listing_url(url)
            ):
                event_urls.add(url)
    return sorted(event_urls)


def event_json(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get('@type') == 'Event':
                return candidate
    return {}


def parse_start(value):
    if not value:
        return None, None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None, None
    return parsed.date().isoformat(), parsed.strftime('%H:%M')


def detail_description(soup, fallback=None):
    details = soup.select_one('#concert-details')
    if not details:
        return clean_text(fallback) or None

    details = BeautifulSoup(str(details), 'html.parser')
    for selector in (
        'nav', '.cta', '.concert-nav', '.concert-quote', '.venue-map',
        'script', 'style', 'form', 'button',
    ):
        for element in details.select(selector):
            element.decompose()
    text = clean_text(details)
    # Remove recurring controls while retaining the programme and prose.
    ignored = {
        'PREVIOUS CONCERT', 'NEXT CONCERT', 'BOOK NOW', 'BACK TO CONCERT LISTING',
        'VIEW SEATING PLAN', 'OPEN SEATING PLAN', 'Click here for map & Directions',
    }
    lines = [line for line in text.splitlines() if line.strip() not in ignored]
    return clean_text('\n'.join(lines)) or clean_text(fallback) or None


def make_record(url, soup):
    event = event_json(soup)
    banner = soup.select_one('#banner')
    title = clean_text(event.get('name'))
    if not title and banner:
        title = clean_text(banner.select_one('h1'))

    event_date, time_from = parse_start(event.get('startDate'))
    location = event.get('location') or {}
    venue = clean_text(location.get('name')) if isinstance(location, dict) else clean_text(location)
    if not venue and banner:
        paragraphs = [clean_text(item) for item in banner.select('.banner-strapline > p')]
        venue = next((item for item in reversed(paragraphs) if item), '')

    if not title or not event_date or not venue:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': CITY,
        'country_code': 'GB',
        'description': detail_description(soup, event.get('description')),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = discover_event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = make_record(url, future.result())
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

    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'] or '', record['title'], record['url']),
    )


class TempleMusicOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='templemusic_org',
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
    TempleMusicOrgCrawler().run()


if __name__ == '__main__':
    main()
