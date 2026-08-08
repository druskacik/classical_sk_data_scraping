import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.waopera.asn.au/'
SOURCE = 'West Australian Opera'
BOOK_URL = urljoin(SOURCE_URL, 'book')
HISTORY_URL = urljoin(SOURCE_URL, 'book/history')
PAST_EVENTS_API = 'https://cms.waopera.asn.au/api/Events/PastEvents'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

# Several CMS venue records omit an address. These event-specific defaults are
# limited to venues whose location is unambiguous on the WA Opera site.
VENUE_CITIES = {
    'Albany Entertainment Centre': 'Albany',
    'Eileen Joyce Studio': 'Perth',
    'Garum Restaurant': 'Perth',
    "His Majesty's Theatre": 'Perth',
    'Mary Street Bakery': 'Perth',
    'Queens Park Theatre Geraldton': 'Geraldton',
    'RAC Arena': 'Perth',
    'Red Earth Arts Precinct Theatre': 'Karratha',
    'Secret Venue': 'Perth',
    'The Exchange in Carnamah': 'Carnamah',
    'Winthrop Hall': 'Perth',
}

TIME_RE = re.compile(r'^\s*(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\s*$', re.I)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_page(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    payload = soup.select_one('script#__NEXT_DATA__')
    if not payload or not payload.string:
        raise ValueError('Page does not contain Next.js event data')
    return json.loads(payload.string)['props']['pageProps']['page']


def event_list_properties(page):
    items = page.get('properties', {}).get('contentGrid', {}).get('items', [])
    for item in items:
        content = item.get('content') or {}
        if content.get('contentType') == 'eventListTiles':
            return content.get('properties') or {}
    raise ValueError('Page does not contain an event listing')


def discover_event_urls(session):
    current = event_list_properties(fetch_page(session, BOOK_URL))
    history = event_list_properties(fetch_page(session, HISTORY_URL))
    events = list(current.get('ssrWhatsOnEventsData', {}).get('events', []))
    history_data = history.get('ssrWhatsOnEventsData', {})
    events.extend(history_data.get('events', []))

    folders = [item.get('id') for item in history.get('events', []) if item.get('id')]
    has_more = bool(history_data.get('hasMore'))
    page_number = 2
    while has_more:
        response = session.post(
            PAST_EVENTS_API,
            json={'eventFolders': folders, 'pageNumber': page_number},
            timeout=60,
        )
        response.raise_for_status()
        result = response.json().get('result') or {}
        events.extend(result.get('events') or [])
        has_more = bool(result.get('hasMore'))
        page_number += 1

    return sorted({
        urljoin(SOURCE_URL, event['route'])
        for event in events
        if event.get('route')
    })


def parse_time(value):
    match = TIME_RE.match(value or '')
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'pm':
        hour += 12
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def parse_date(value):
    try:
        return date.fromisoformat((value or '')[:10]).isoformat()
    except ValueError:
        return None


def event_description(properties):
    parts = []
    for value in (
        properties.get('eventSubheading'),
        properties.get('seoMetaDescription'),
    ):
        text = clean_text(value)
        if text and text not in parts:
            parts.append(text)

    def collect_markup(value):
        if isinstance(value, dict):
            markup = clean_text(value.get('markup'))
            if markup and markup not in parts:
                parts.append(markup)
            for child in value.values():
                collect_markup(child)
        elif isinstance(value, list):
            for child in value:
                collect_markup(child)

    collect_markup(properties.get('contentGrid'))
    collect_markup(properties.get('eventNotes'))
    return '\n\n'.join(parts) or None


def parse_event_page(page, url):
    properties = page.get('properties') or {}
    title = clean_text(properties.get('eventHeading') or page.get('name'))
    description = event_description(properties)
    records = []

    for performance in (properties.get('performances') or {}).get('items', []):
        performance_properties = (
            (performance.get('content') or {}).get('properties') or {}
        )
        venue_data = performance_properties.get('venues') or {}
        venue = clean_text(venue_data.get('name'))
        city = VENUE_CITIES.get(venue)
        # Online-only and otherwise geographically ambiguous entries are not
        # valid concert records for this country-scoped crawler.
        if not title or not venue or not city:
            continue

        for date_item in (performance_properties.get('dates') or {}).get('items', []):
            date_properties = (
                (date_item.get('content') or {}).get('properties') or {}
            )
            event_date = parse_date(date_properties.get('dateFrom'))
            if not event_date:
                continue
            sessions = (date_properties.get('sessions') or {}).get('items', [])
            if not sessions:
                sessions = [None]
            for session in sessions:
                session_properties = (
                    ((session or {}).get('content') or {}).get('properties') or {}
                )
                records.append({
                    'title': title,
                    'date': event_date,
                    'url': url,
                    'time_from': parse_time(session_properties.get('time')),
                    'venue': venue,
                    'city': city,
                    'country_code': 'AU',
                    'description': description,
                    'source_url': SOURCE_URL,
                    'source': SOURCE,
                })
    return records


class WaoperaAsnAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='waopera_asn_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        urls = discover_event_urls(session)
        records = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(fetch_page, session, url): url for url in urls
            }
            for future in as_completed(futures):
                url = futures[future]
                try:
                    records.extend(parse_event_page(future.result(), url))
                except (requests.RequestException, KeyError, TypeError, ValueError) as error:
                    log_message(
                        'Failed to scrape WA Opera event detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(
            records,
            key=lambda item: (
                item['date'], item['time_from'] or '', item['title'],
                item['venue'], item['url'],
            ),
        )


def main():
    WaoperaAsnAuCrawler().run()


if __name__ == '__main__':
    main()
