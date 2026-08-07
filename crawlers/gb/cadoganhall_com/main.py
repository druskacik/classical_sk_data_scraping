import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://cadoganhall.com/'
EVENTS_URL = urljoin(SOURCE_URL, 'whats-on/?results=all')
SOURCE = 'Cadogan Hall'
VENUE = 'Cadogan Hall'
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
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    # Some editor content contains escaped HTML markup inside text nodes.
    text = BeautifulSoup(text, 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def listing_urls(session):
    response = session.get(EVENTS_URL, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    urls = set()
    for item in soup.select('.c-event-item'):
        link = item.select_one('.c-event-item__heading a[href], a.c-event-item__link-wrap[href]')
        if not link:
            continue
        url = urljoin(SOURCE_URL, link.get('href', '')).split('#', 1)[0]
        path = urlparse(url).path
        if path.startswith('/whats-on/') and path.rstrip('/') != '/whats-on':
            urls.add(url)
    return sorted(urls)


def description_from(soup):
    parts = []
    for selector in ('.prod__programme', '.c-prod__info .c-content-style'):
        for element in soup.select(selector):
            text = clean_text(element)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def parse_performance(element):
    value = clean_text(element.select_one('.atc_date_start'))
    try:
        start = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return start.date().isoformat(), start.strftime('%H:%M')


def records_from_page(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    title = clean_text(soup.select_one('.c-prod__heading'))
    if not title:
        return []

    description = description_from(soup)
    records = []
    seen = set()
    for performance in soup.select('.atc_event'):
        parsed = parse_performance(performance)
        if not parsed or parsed in seen:
            continue
        seen.add(parsed)
        event_date, time_from = parsed
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': VENUE,
            'city': CITY,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def fetch_records(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return records_from_page(url, response.text)


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda item: (item['date'], item['time_from'] or '', item['title'], item['url']),
    )


class CadoganHallComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cadoganhall_com',
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
    CadoganHallComCrawler().run()


if __name__ == '__main__':
    main()
