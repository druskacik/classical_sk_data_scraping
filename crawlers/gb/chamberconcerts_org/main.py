import html
import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.chamberconcerts.org/'
EVENTS_URL = urljoin(SOURCE_URL, 'whatson')
EVENTS_JSON_URL = f'{EVENTS_URL}?format=json'
SOURCE = 'Manchester Chamber Concerts Society'
VENUE = 'The Stoller Hall'
CITY = 'Manchester'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-GB,en;q=0.9',
}
LOCAL_TIMEZONE = ZoneInfo('Europe/London')


def clean_text(value):
    if not value:
        return ''
    soup = BeautifulSoup(str(value), 'html.parser')
    for element in soup.select(
        'script, style, noscript, .image-block, .button-block, .sqs-block-button-container'
    ):
        element.decompose()
    text = soup.get_text('\n', strip=True)
    text = html.unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def event_datetime(event):
    timestamp = event.get('startDate')
    if not isinstance(timestamp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(timestamp / 1000, LOCAL_TIMEZONE)
    except (OSError, OverflowError, ValueError):
        return None


def parse_event(event):
    title = clean_text(event.get('title'))
    starts_at = event_datetime(event)
    path = event.get('fullUrl') or ''
    url = urljoin(SOURCE_URL, path)
    if not title or starts_at is None or not path:
        return None

    return {
        'title': title,
        'date': starts_at.date().isoformat(),
        'url': url,
        'time_from': starts_at.strftime('%H:%M'),
        'venue': VENUE,
        'city': CITY,
        'country_code': 'GB',
        'description': clean_text(event.get('body')) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class ChamberConcertsOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='chamberconcerts_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
        try:
            response = requests.get(EVENTS_JSON_URL, headers=HEADERS, timeout=45)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch Manchester Chamber Concerts events',
                event='crawler_fetch_failed',
                level='error',
                url=EVENTS_JSON_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        events = (payload.get('past') or []) + (payload.get('upcoming') or [])
        records = []
        for event in events:
            record = parse_event(event)
            if record:
                records.append(record)

        unique = {
            (record['title'], record['date'], record['time_from'], record['venue']): record
            for record in records
        }
        return sorted(
            unique.values(),
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    ChamberConcertsOrgCrawler().run()


if __name__ == '__main__':
    main()
