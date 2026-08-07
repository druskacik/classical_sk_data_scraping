from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.promusica-hannover.de/de'
EVENTS_API = f'{SOURCE_URL}/api/productions/'
CALENDAR_API = f'{SOURCE_URL}/api/productions/calendar/'
SOURCE = 'PROMUSICA Hannover'
CITY = 'Hannover'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}


def clean_html(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    lines = [' '.join(line.split()) for line in text.splitlines()]
    return '\n'.join(line for line in lines if line).strip()


def fetch_events(session):
    """Fetch all API pages, including the past events retained by the site."""
    calendar_response = session.get(CALENDAR_API, timeout=45)
    calendar_response.raise_for_status()
    available_dates = calendar_response.json()
    parsed_dates = []
    for value in available_dates:
        try:
            parsed_dates.append(datetime.strptime(value, '%d.%m.%Y'))
        except (TypeError, ValueError):
            continue

    url = EVENTS_API
    params = {'date': min(parsed_dates).strftime('%d.%m.%Y')} if parsed_dates else None
    events = []
    while url:
        response = session.get(url, params=params, timeout=45)
        response.raise_for_status()
        payload = response.json()
        events.extend(payload.get('results') or [])
        url = payload.get('next')
        params = None
    return events


def event_description(event):
    parts = []
    for heading, field in (
        ('Programm', 'program'),
        ('Mitwirkende', 'interpreter_text'),
        (None, 'program_text'),
    ):
        value = clean_html(event.get(field))
        if not value:
            continue
        part = f'{heading}\n{value}' if heading else value
        if part not in parts:
            parts.append(part)
    return '\n\n'.join(parts) or None


def make_record(event):
    title = clean_html(event.get('title'))
    subtitle = clean_html(event.get('subtitle'))
    if subtitle and subtitle.lower() not in title.lower():
        title = f'{title} – {subtitle}'

    url = event.get('get_absolute_url') or ''
    room = event.get('room') or {}
    venue = clean_html(room.get('display_name') or room.get('name'))
    try:
        start = datetime.fromisoformat(event.get('date') or '')
    except (TypeError, ValueError):
        return None

    if not title or not url or not venue:
        return None

    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': url,
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': CITY,
        'country_code': 'DE',
        'description': event_description(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = fetch_events(session)
    records = []
    for event in events:
        record = make_record(event)
        if record:
            records.append(record)
        else:
            log_message(
                'Skipped concert with incomplete required fields',
                event='crawler_item_skipped',
                level='warning',
                url=event.get('get_absolute_url') or EVENTS_API,
                event_id=event.get('id'),
            )
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class PromusicaHannoverDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='promusica_hannover_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
    PromusicaHannoverDeCrawler().run()


if __name__ == '__main__':
    main()
