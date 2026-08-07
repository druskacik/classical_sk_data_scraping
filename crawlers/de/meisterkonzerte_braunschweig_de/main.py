import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.meisterkonzerte-braunschweig.de/de'
EVENTS_API_URL = f'{SOURCE_URL}/api/productions/'
SOURCE = 'Meister Konzerte Braunschweig'
CITY = 'Braunschweig'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}


def clean_html(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session


def description_from_event(event):
    sections = []
    for heading, field in (
        ('Programm', 'program'),
        ('Besetzung', 'interpreter_text'),
        ('Beschreibung', 'program_text'),
        ('Hinweis', 'end_program'),
    ):
        text = clean_html(event.get(field))
        if text:
            sections.append(f'{heading}\n{text}')
    return '\n\n'.join(sections) or None


def make_record(event):
    title = clean_html(event.get('title'))
    url = str(event.get('get_absolute_url') or '').strip()
    venue = clean_html((event.get('room') or {}).get('display_name'))
    start = str(event.get('date') or '')
    try:
        start_at = datetime.fromisoformat(start)
    except ValueError:
        return None

    if not title or not url or not venue:
        return None

    return {
        'title': title,
        'date': start_at.date().isoformat(),
        'url': url,
        'time_from': start_at.strftime('%H:%M'),
        'venue': venue,
        'city': CITY,
        'country_code': 'DE',
        'description': description_from_event(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = make_session()
    # Supplying an early date makes the API include every retained past event;
    # without it, the endpoint returns upcoming concerts only.
    url = EVENTS_API_URL
    params = {'date': '01.01.1900'}
    records = []

    while url:
        try:
            response = session.get(url, params=params, timeout=45)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to scrape concert API page',
                event='crawler_page_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            break

        for event in payload.get('results', []):
            record = make_record(event)
            if record:
                records.append(record)

        url = payload.get('next')
        params = None

    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'] or '', record['title']),
    )


class MeisterkonzerteBraunschweigDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='meisterkonzerte_braunschweig_de',
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
    MeisterkonzerteBraunschweigDeCrawler().run()


if __name__ == '__main__':
    main()
