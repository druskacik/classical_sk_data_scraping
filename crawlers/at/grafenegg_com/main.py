import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.grafenegg.com/en'
EVENTS_URL = f'{SOURCE_URL}/api/events/'
PROGRAMME_URL = f'{SOURCE_URL}/programme-tickets'
SOURCE = 'Grafenegg'
CITY = 'Grafenegg'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-GB,en;q=0.9,de-AT;q=0.7',
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
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def listing_events(session):
    # The public API clamps old dates to its currently published catalogue, so
    # requesting the earliest possible date captures everything it exposes.
    url = EVENTS_URL
    params = {'date': '1900-01-01T00:00:00Z', 'page_size': 100}
    events = []
    while url:
        payload = get_json(session, url, params=params)
        events.extend(payload.get('results') or [])
        url = payload.get('next')
        params = None
    return events


def event_url(event):
    slug = event.get('slug') or {}
    slug = slug.get('en') if isinstance(slug, dict) else slug
    event_id = event.get('id')
    if not slug or not event_id:
        return ''
    return f'{PROGRAMME_URL}/{slug}/{event_id}'


def resolve_location(event):
    room = event.get('room') or {}
    room_name = clean_text(room.get('name'))
    venue_data = room.get('venue') or {}
    venue_name = clean_text(venue_data.get('name'))
    address = clean_text(venue_data.get('address'))
    venue = room_name or venue_name
    if not venue:
        return None, None

    # This is Grafenegg's venue calendar. Avoid applying its home-city default
    # to records explicitly marked as touring or to an external venue address.
    if event.get('is_on_tour'):
        return None, None
    if address and 'grafenegg' not in address.lower():
        return None, None
    return venue, CITY


def work_text(work):
    composer = clean_text(work.get('name'))
    title = clean_text(work.get('description'))
    if composer and title:
        return f'{composer}: {title}'
    return composer or title


def detail_description(event, fallback=None):
    parts = []
    for key in ('description_long', 'description_short', 'description_short2'):
        value = clean_text(event.get(key))
        if value and value not in parts:
            parts.append(value)

    works = [work_text(work) for work in event.get('works') or [] if not work.get('is_break')]
    works = [work for work in works if work]
    if works:
        parts.append('Programme\n' + '\n'.join(works))

    return clean_text('\n\n'.join(parts)) or clean_text(fallback) or None


def make_record(event, detail=None):
    detail = detail or event
    title = clean_text(detail.get('name') or event.get('name'))
    subtitle = clean_text(detail.get('subtitle') or event.get('subtitle'))
    if subtitle and subtitle.lower() not in title.lower():
        title = f'{title} – {subtitle}'

    start = detail.get('date_start') or event.get('date_start') or ''
    match = re.match(r'(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})', start)
    url = event_url(detail) or event_url(event)
    venue, city = resolve_location(detail)
    if not title or not match or not url or not venue or not city:
        return None
    try:
        event_date = date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': f'{match.group(2)}:{match.group(3)}',
        'venue': venue,
        'city': city,
        'country_code': 'AT',
        'description': detail_description(detail, event.get('description_short')),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = listing_events(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(get_json, session, f'{EVENTS_URL}{event["id"]}/'): event
            for event in events
            if event.get('id') and event.get('has_detail')
        }
        detailed_ids = {event['id'] for event in futures.values()}
        for event in events:
            if event.get('id') not in detailed_ids:
                record = make_record(event)
                if record:
                    records.append(record)

        for future in as_completed(futures):
            event = futures[future]
            try:
                record = make_record(event, future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape Grafenegg event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=event_url(event),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                record = make_record(event)
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class GrafeneggComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='grafenegg_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
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
    GrafeneggComCrawler().run()


if __name__ == '__main__':
    main()
