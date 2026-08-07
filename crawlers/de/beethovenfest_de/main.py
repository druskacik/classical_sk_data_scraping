import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.beethovenfest.de/de'
EVENTS_API = f'{SOURCE_URL}/api/events/'
PROGRAM_URL = f'{SOURCE_URL}/programm-tickets'
SOURCE = 'Beethovenfest Bonn'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.json()


def listing_events(session):
    # The API is the feed used by the programme page's infinite scroll.
    url = EVENTS_API
    events = []
    while url:
        payload = get_json(session, url)
        events.extend(payload.get('results') or [])
        url = payload.get('next')
    return events


def event_url(event):
    slug = event.get('slug') or {}
    slug = slug.get('de') if isinstance(slug, dict) else slug
    event_id = event.get('id')
    if not slug or not event_id:
        return ''
    return f'{PROGRAM_URL}/{slug}/{event_id}'


def description_text(event):
    parts = []
    body = clean_text(event.get('description'))
    if body:
        parts.append(body)

    programme = []
    for item in event.get('event_compositions') or []:
        composition = item.get('composition') or {}
        composer = clean_text(composition.get('composer'))
        work = clean_text(composition.get('description'))
        line = ': '.join(value for value in (composer, work) if value)
        if line:
            programme.append(line)

    program_text = clean_text(event.get('program_text'))
    if programme:
        parts.append('Programm\n' + '\n'.join(programme))
    if program_text:
        parts.append(program_text)
    return '\n\n'.join(parts) or None


def make_record(event):
    title = clean_text(event.get('title'))
    url = event_url(event)
    start = event.get('date_and_time') or ''
    venue_data = event.get('venue_obj') or {}
    venue = clean_text(venue_data.get('name'))
    city = clean_text(venue_data.get('city'))
    if not title or not url or not venue or not city:
        return None

    try:
        starts_at = datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return None

    return {
        'title': title,
        'date': starts_at.date().isoformat(),
        'url': url,
        'time_from': starts_at.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'DE',
        'description': description_text(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = listing_events(session)
    records = []

    # Detail responses contain the full programme and venue city, whereas the
    # list feed deliberately returns abbreviated versions of both.
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(get_json, session, f'{EVENTS_API}{event["id"]}/'): event
            for event in events
            if event.get('id')
        }
        for future in as_completed(futures):
            event = futures[future]
            try:
                detail = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape Beethovenfest event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=event_url(event),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            record = make_record(detail)
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class BeethovenfestDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='beethovenfest_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
    BeethovenfestDeCrawler().run()


if __name__ == '__main__':
    main()
