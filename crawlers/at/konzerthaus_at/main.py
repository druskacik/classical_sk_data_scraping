import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://konzerthaus.at/de'
PROGRAM_URL = f'{SOURCE_URL}/programm-und-karten'
EVENTS_API = f'{SOURCE_URL}/api/events/'
SOURCE = 'Wiener Konzerthaus'
CITY = 'Wien'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
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
    # The public programme API requires a starting date and includes every
    # event from that date onward. Its response is paginated.
    url = EVENTS_API
    params = {'date': date.today().isoformat(), 'page_size': 100}
    events = []
    while url:
        payload = get_json(session, url, params=params)
        events.extend(payload.get('results') or [])
        url = payload.get('next')
        params = None
    return events


def event_url(event):
    slug = event.get('slug') or {}
    slug = slug.get('de') if isinstance(slug, dict) else slug
    if not slug or not event.get('id'):
        return ''
    return f'{PROGRAM_URL}/{slug}/{event["id"]}'


def resolve_location(event):
    room = event.get('room') or {}
    venue_data = room.get('venue') or {}
    room_name = clean_text(room.get('name'))
    venue_name = clean_text(venue_data.get('name'))
    if not room_name and not venue_name:
        return None, None

    # The calendar occasionally lists partner locations. The venue supplied
    # by the API remains authoritative; the home-city default applies only to
    # Vienna venues, never to an explicitly named touring location.
    venue = room_name or venue_name
    location_text = f'{venue_name} {venue}'.lower()
    if any(term in location_text for term in ('wien', 'konzerthaus', 'musikverein')):
        return venue, CITY
    return None, None


def work_text(work):
    composer = clean_text(work.get('description_short'))
    name = clean_text(work.get('name'))
    if not name or name == '***':
        return ''
    return f'{composer}: {name}' if composer and composer != '***' else name


def detail_description(detail, fallback=None):
    parts = []
    for key in ('description_long', 'description_short', 'description_short2'):
        value = clean_text(detail.get(key))
        if value and value not in parts:
            parts.append(value)

    works = [work_text(work) for work in detail.get('works') or []]
    works = [work for work in works if work]
    if works:
        parts.append('Programm\n' + '\n'.join(works))

    return clean_text('\n\n'.join(parts)) or clean_text(fallback) or None


def make_record(event, detail=None):
    detail = detail or event
    title = clean_text(detail.get('name') or event.get('name'))
    subtitle = clean_text(detail.get('subtitle') or event.get('subtitle'))
    if subtitle and subtitle.lower() not in title.lower():
        title = f'{title} – {subtitle}'

    start = detail.get('date_start') or event.get('date_start') or ''
    match = re.match(r'(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})', start)
    venue, city = resolve_location(detail)
    url = event_url(detail) or event_url(event)
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
            executor.submit(get_json, session, f'{EVENTS_API}{event["id"]}/'): event
            for event in events if event.get('id')
        }
        for future in as_completed(futures):
            event = futures[future]
            try:
                record = make_record(event, future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape concert detail',
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


class KonzerthausAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='konzerthaus_at',
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
    KonzerthausAtCrawler().run()


if __name__ == '__main__':
    main()
