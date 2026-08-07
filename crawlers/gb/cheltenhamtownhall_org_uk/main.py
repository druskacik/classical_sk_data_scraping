import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://cheltenhamtownhall.org.uk/'
SOURCE = 'Cheltenham Town Hall'
EVENTS_API = f'{SOURCE_URL}wp-json/tribe/events/v1/events'
CITY = 'Cheltenham'

HEADERS = {
    'Accept': 'application/json',
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
}


def clean_text(value):
    if not value:
        return ''
    value = str(value)
    text = (
        BeautifulSoup(value, 'html.parser').get_text('\n', strip=True)
        if '<' in value
        else value
    )
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_events(session):
    # The Events Calendar API defaults to future events. An early start date
    # includes the site's still-published archive as well as its current diary.
    url = EVENTS_API
    params = {'start_date': '2000-01-01 00:00:00', 'per_page': 50}
    events = []
    while url:
        response = session.get(url, params=params, timeout=45)
        response.raise_for_status()
        payload = response.json()
        events.extend(payload.get('events') or [])
        url = payload.get('next_rest_url')
        params = None
    return events


def make_record(event):
    title = clean_text(event.get('title'))
    url = clean_text(event.get('url'))
    venue_data = event.get('venue') or {}
    venue = clean_text(venue_data.get('venue'))
    city = clean_text(venue_data.get('city')) or CITY
    start = event.get('start_date') or ''

    try:
        starts_at = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None

    if not title or not url or not venue or not city:
        return None

    return {
        'title': title,
        'date': starts_at.date().isoformat(),
        'url': url,
        'time_from': None if event.get('all_day') else starts_at.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': clean_text(event.get('description')) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class CheltenhamTownHallCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cheltenhamtownhall_org_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            events = fetch_events(session)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch event catalogue',
                event='crawler_fetch_failed',
                level='error',
                url=EVENTS_API,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        records = [record for item in events if (record := make_record(item))]
        return sorted(
            records,
            key=lambda record: (
                record['date'],
                record['time_from'] or '',
                record['title'],
                record['url'],
            ),
        )


def main():
    CheltenhamTownHallCrawler().run()


if __name__ == '__main__':
    main()
