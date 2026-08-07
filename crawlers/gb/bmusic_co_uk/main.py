import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://bmusic.co.uk/'
SOURCE = 'B:Music'
SEARCH_URL = 'https://gxmc4aox8r-dsn.algolia.net/1/indexes/*/queries'
SEARCH_INDEX = 'main_instance_dates'
ALGOLIA_APP_ID = 'GXMC4AOX8R'
ALGOLIA_API_KEY = '9ef8d9deccca3109d17e013a0246d456'
CITY = 'Birmingham'
TIMEZONE = ZoneInfo('Europe/London')

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
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def search_events(session):
    # The public What's On page uses this Algolia index. Omitting its
    # future-only filter also returns event pages retained in the archive.
    search_params = urlencode({
        'hitsPerPage': 1000,
        'page': 0,
        'filters': '(type:performances OR type:cbso)',
    })
    response = session.post(
        SEARCH_URL,
        params={
            'x-algolia-application-id': ALGOLIA_APP_ID,
            'x-algolia-api-key': ALGOLIA_API_KEY,
        },
        json={'requests': [{'indexName': SEARCH_INDEX, 'params': search_params}]},
        timeout=45,
    )
    response.raise_for_status()
    results = response.json().get('results') or []
    return (results[0].get('hits') or []) if results else []


def detail_data(session, event):
    url = urljoin(SOURCE_URL, event.get('uri') or '')
    if not url.startswith(urljoin(SOURCE_URL, 'events/')):
        return None
    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')

    title = clean_text(soup.select_one('.c-event-header__heading')) or clean_text(
        event.get('title')
    )
    venue = clean_text(soup.select_one('.c-event-header__title > span'))
    if not venue:
        venues = event.get('venues') or []
        venue = clean_text(venues[0]) if venues else ''

    overview = soup.select_one('#content-overview')
    if overview:
        heading = overview.select_one('h2')
        if heading:
            heading.decompose()
    description = clean_text(overview) or clean_text(event.get('text_content')) or None

    return {'url': url, 'title': title, 'venue': venue, 'description': description}


def event_records(event, detail):
    if not detail or not detail['title'] or not detail['venue']:
        return []

    # Most listings are at B:Music's Birmingham halls. Preserve the explicit
    # city on the occasional off-site venue instead of applying that default.
    city = 'Wolverhampton' if 'wolverhampton' in detail['venue'].casefold() else CITY
    records = []
    for timestamp in sorted(set(event.get('instanceDates') or [])):
        try:
            start = datetime.fromtimestamp(int(timestamp), TIMEZONE)
        except (OSError, OverflowError, TypeError, ValueError):
            continue
        records.append({
            'title': detail['title'],
            'date': start.date().isoformat(),
            'url': detail['url'],
            'time_from': start.strftime('%H:%M'),
            'venue': detail['venue'],
            'city': city,
            'country_code': 'GB',
            'description': detail['description'],
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = search_events(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(detail_data, session, event): event for event in events}
        for future in as_completed(futures):
            event = futures[future]
            try:
                records.extend(event_records(event, future.result()))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape B:Music event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=urljoin(SOURCE_URL, event.get('uri') or ''),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class BmusicCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bmusic_co_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    BmusicCoUkCrawler().run()


if __name__ == '__main__':
    main()
