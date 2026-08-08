import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.cityrecitalhall.com/'
SOURCE = 'City Recital Hall'
EVENTS_API = urljoin(SOURCE_URL, 'umbraco/Api/SearchApi/WhatsOn')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/html;q=0.9',
    'Accept-Language': 'en-AU,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url, params=None):
    response = session.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def listing_events(session):
    # This is the endpoint used by the public What's On filters. A large page
    # size avoids relying on the browser's "load more" control.
    payload = get_json(
        session,
        EVENTS_API,
        params={
            'dates': '',
            'genres': '',
            'range': 'upcoming',
            'page': 1,
            'pageSize': 1000,
            'isOpenEnded': 'false',
        },
    )
    return payload.get('items') or []


def parse_location(html):
    soup = BeautifulSoup(html, 'html.parser')
    venue_node = soup.select_one('[data-test="venue name"]')
    address_node = soup.select_one('[data-test="venue address"]')
    venue = clean_text(venue_node.get_text(' ', strip=True) if venue_node else '')
    address = clean_text(address_node.get_text(' ', strip=True) if address_node else '')
    if not venue:
        return None, None

    match = re.search(r'(?:,|\s)\s*([A-Za-z][A-Za-z .\'’-]+?)\s+NSW\s+\d{4}\b', address)
    city = clean_text(match.group(1)).strip(' ,') if match else ''
    # The venue-specific calendar and its full street address provide strong
    # evidence for this default, but only for the named home venue.
    if not city and venue.lower() == 'city recital hall':
        city = 'Sydney'
    return venue or None, city or None


def event_datetimes(values):
    datetimes = []
    for value in clean_text(values.get('eventShowDateTimes')).split():
        try:
            datetimes.append(datetime.strptime(value, '%Y%m%d%H%M'))
        except ValueError:
            continue
    return datetimes


def description(values):
    parts = []
    for key in ('mainContent', 'programDetail'):
        text = clean_text(values.get(key))
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def make_records(event, venue, city):
    values = event.get('values') or {}
    title = clean_text(values.get('eventName') or values.get('nodeName'))
    relative_url = clean_text(values.get('url'))
    url = urljoin(SOURCE_URL, relative_url) if relative_url else ''
    if not title or not url or not venue or not city:
        return []

    records = []
    for start in event_datetimes(values):
        records.append(
            {
                'title': title,
                'date': start.date().isoformat(),
                'url': url,
                'time_from': start.strftime('%H:%M'),
                'venue': venue,
                'city': city,
                'country_code': 'AU',
                'description': description(values),
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = listing_events(session)
    records = []

    def fetch_detail(event):
        values = event.get('values') or {}
        url = urljoin(SOURCE_URL, values.get('url') or '')
        response = session.get(url, timeout=60)
        response.raise_for_status()
        return url, parse_location(response.text)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_detail, event): event for event in events}
        for future in as_completed(futures):
            event = futures[future]
            values = event.get('values') or {}
            url = urljoin(SOURCE_URL, values.get('url') or '')
            try:
                _, (venue, city) = future.result()
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
            records.extend(make_records(event, venue, city))

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class CityRecitalHallComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cityrecitalhall_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
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
    CityRecitalHallComCrawler().run()


if __name__ == '__main__':
    main()
