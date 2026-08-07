import json
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://tenet.nyc/'
CONCERTS_URL = urljoin(SOURCE_URL, 'concerts')
SOURCE = 'Tenet Vocal Artists'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def concert_urls(soup):
    urls = set()
    # The season is rendered as one article per programme. Detail pages contain
    # a MusicEvent JSON-LD object for each individual performance.
    for article in soup.select('article'):
        for link in article.select('a[href]'):
            url = urljoin(CONCERTS_URL, link.get('href'))
            parsed = urlparse(url)
            if parsed.netloc == 'tenet.nyc' and parsed.path not in ('/', '/concerts'):
                urls.add(url.split('#', 1)[0])
    return sorted(urls)


def json_ld_items(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict):
                yield item


def parse_start(value):
    if not isinstance(value, str):
        return None, None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None, None
    return parsed.date().isoformat(), parsed.strftime('%H:%M') if 'T' in value else None


def make_record(item, page_url):
    event_type = item.get('@type')
    if event_type not in ('Event', 'MusicEvent'):
        return None

    location = item.get('location') or {}
    address = location.get('address') or {}
    title = str(item.get('name') or '').strip()
    venue = str(location.get('name') or '').strip()
    city = str(address.get('addressLocality') or '').strip()
    country = address.get('addressCountry') or 'US'
    if isinstance(country, dict):
        country = country.get('name') or country.get('@id') or ''
    country = str(country).strip().upper()
    if country in ('UNITED STATES', 'USA'):
        country = 'US'

    event_date, time_from = parse_start(item.get('startDate'))
    url = urljoin(page_url, item.get('url') or page_url)
    if not all((title, event_date, url, venue, city)) or country != 'US':
        return None

    description = str(item.get('description') or '').strip() or None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country,
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    listing = get_soup(session, CONCERTS_URL)
    records = []
    for url in concert_urls(listing):
        try:
            detail = get_soup(session, url)
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
        records.extend(
            record
            for item in json_ld_items(detail)
            if (record := make_record(item, url)) is not None
        )

    unique = {
        (record['title'], record['date'], record['time_from'], record['venue']): record
        for record in records
    }
    return sorted(
        unique.values(),
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class TenetNycCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='tenet_nyc',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
        upload_target='classical',
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
    TenetNycCrawler().run()


if __name__ == '__main__':
    main()
