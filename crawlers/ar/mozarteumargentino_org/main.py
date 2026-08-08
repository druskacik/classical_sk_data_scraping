import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.mozarteumargentino.org/'
EVENTS_API = f'{SOURCE_URL}wp-json/tribe/events/v1/events'
SOURCE = 'Mozarteum Argentino'
CITY = 'Buenos Aires'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'es-AR,es;q=0.9,en;q=0.8',
    'Referer': f'{SOURCE_URL}eventos/',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def listing_events(session):
    # An explicit old start date makes the Tribe API include past events that
    # remain published. Follow its REST pagination in case the archive grows.
    url = EVENTS_API
    params = {'per_page': 50, 'start_date': '1900-01-01'}
    events = []
    while url:
        payload = get_json(session, url, params=params)
        events.extend(payload.get('events') or [])
        url = payload.get('next_rest_url')
        params = None
    return events


def page_description(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    container = soup.select_one('.ast-container')
    return clean_text(container) if container else ''


def is_concert(event):
    categories = {item.get('slug') for item in event.get('categories') or []}
    return 'abonos-inscripcion-y-renovacion' not in categories


def make_record(event, detail_text=''):
    title = clean_text(event.get('title'))
    url = event.get('url') or event.get('website') or ''
    venue = clean_text((event.get('venue') or {}).get('venue'))
    start = event.get('start_date') or ''
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):\d{2}', start)
    if not title or not url or not venue or not match:
        return None
    try:
        event_date = date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None

    description_parts = [
        detail_text,
        clean_text(event.get('description')),
        clean_text(event.get('excerpt')),
    ]
    description = '\n\n'.join(part for part in description_parts if part) or None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': f'{match.group(2)}:{match.group(3)}',
        'venue': venue,
        'city': CITY,
        'country_code': 'AR',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class MozarteumArgentinoOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mozarteumargentino_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AR',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            events = [event for event in listing_events(session) if is_concert(event)]
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch Mozarteum Argentino events',
                event='crawler_fetch_failed',
                level='error',
                url=EVENTS_API,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        records = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(page_description, session, event.get('url')): event
                for event in events if event.get('url')
            }
            for future in as_completed(futures):
                event = futures[future]
                try:
                    description = future.result()
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch Mozarteum Argentino event detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=event.get('url'),
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                    description = ''
                record = make_record(event, description)
                if record:
                    records.append(record)

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    MozarteumArgentinoOrgCrawler().run()


if __name__ == '__main__':
    main()
