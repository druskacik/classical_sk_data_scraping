import html
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.budleighmusicfestival.co.uk/'
SOURCE = 'Budleigh Music Festival'
API_URL = f'{SOURCE_URL}wp-json/tribe/events/v1/events'
CITY = 'Budleigh Salterton'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_html(value):
    if not value:
        return ''
    text = BeautifulSoup(html.unescape(str(value)), 'html.parser').get_text('\n', strip=True)
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())


def valid_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date().isoformat()
    except (TypeError, ValueError):
        return None


def parse_event(event):
    title = clean_html(event.get('title'))
    event_date = valid_date((event.get('start_date') or '')[:10])
    url = (event.get('url') or '').strip()
    venue_data = event.get('venue')
    venue = clean_html(venue_data.get('venue')) if isinstance(venue_data, dict) else ''

    if not title or not event_date or not url or not venue:
        return None

    time_from = None
    if not event.get('all_day'):
        details = event.get('start_date_details') or {}
        hour = details.get('hour')
        minute = details.get('minutes')
        if str(hour).isdigit() and str(minute).isdigit():
            time_from = f'{int(hour):02d}:{int(minute):02d}'

    description = clean_html(event.get('description')) or clean_html(event.get('excerpt')) or None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': CITY,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def detail_description(session, url):
    try:
        response = session.get(url, timeout=45)
        response.raise_for_status()
    except requests.RequestException as error:
        log_message(
            'Failed to fetch Budleigh Music Festival event detail',
            event='crawler_item_failed',
            level='warning',
            url=url,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    # Older migrated events have an empty REST description, but retain their
    # programme and artist notes in the theme's event information tab.
    content = soup.select_one('.tabbed_info .tab_content')
    return clean_html(content) or None


class BudleighMusicFestivalCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='budleighmusicfestival_co_uk',
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
        records = []
        page = 1
        end_year = date.today().year + 2

        while True:
            params = {
                'page': page,
                'per_page': 50,
                'start_date': '2010-01-01 00:00:00',
                'end_date': f'{end_year}-12-31 23:59:59',
                'status': 'publish',
            }
            try:
                response = session.get(API_URL, params=params, timeout=45)
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to fetch Budleigh Music Festival events',
                    event='crawler_fetch_failed',
                    level='error',
                    url=API_URL,
                    page=page,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                raise

            events = payload.get('events', [])
            if not isinstance(events, list):
                raise ValueError('Budleigh Music Festival API returned invalid events data')

            for event in events:
                if isinstance(event, dict):
                    record = parse_event(event)
                    if record:
                        if record['description'] is None:
                            record['description'] = detail_description(session, record['url'])
                        records.append(record)

            total_pages = int(payload.get('total_pages') or 0)
            if page >= total_pages:
                break
            page += 1

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    BudleighMusicFestivalCoUkCrawler().run()


if __name__ == '__main__':
    main()
