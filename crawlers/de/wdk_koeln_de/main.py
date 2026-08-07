from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.wdk-koeln.de/de'
SOURCE = 'Westdeutsche Konzertdirektion'
EVENTS_API = f'{SOURCE_URL}/api/productions/'
CITY = 'Köln'

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
    return BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)


def description_from(event):
    parts = []
    for heading, field in (
        ('Besetzung', 'interpreter_text'),
        ('Programm', 'program'),
        (None, 'program_text'),
    ):
        text = clean_text(event.get(field))
        if text:
            parts.append(f'{heading}\n{text}' if heading else text)
    return '\n\n'.join(parts) or None


def make_record(event):
    title = clean_text(event.get('title'))
    subtitle = clean_text(event.get('subtitle'))
    if subtitle and subtitle.casefold() not in title.casefold():
        title = f'{title} – {subtitle}'

    raw_date = event.get('date')
    try:
        starts_at = datetime.fromisoformat(raw_date) if raw_date else None
    except (TypeError, ValueError):
        return None

    room = event.get('room') or {}
    venue = clean_text(room.get('display_name') or room.get('name'))
    url = event.get('get_absolute_url') or ''
    if not title or not starts_at or not venue or not url:
        return None

    return {
        'title': title,
        'date': starts_at.date().isoformat(),
        'url': url,
        'time_from': starts_at.strftime('%H:%M'),
        'venue': venue,
        'city': CITY,
        'country_code': 'DE',
        'description': description_from(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    url = EVENTS_API
    # An early date makes the API return its still-published archive as well
    # as forthcoming concerts. Without it, only upcoming events are returned.
    params = {'date': '01.01.2000'}
    records = []

    while url:
        try:
            response = session.get(url, params=params, timeout=45)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch concert catalogue',
                event='crawler_page_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        for event in payload.get('results') or []:
            record = make_record(event)
            if record:
                records.append(record)

        url = payload.get('next')
        params = None

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class WdkKoelnDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='wdk_koeln_de',
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
    WdkKoelnDeCrawler().run()


if __name__ == '__main__':
    main()
