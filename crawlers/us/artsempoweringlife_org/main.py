import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://artsempoweringlife.org/ael-events/'
EVENTS_API = 'https://artsempoweringlife.org/wp-json/tribe/events/v1/events'
SOURCE = 'Arts Empowering Life'

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
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_events(session):
    records = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        response = session.get(
            EVENTS_API,
            params={'per_page': 100, 'page': page},
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        records.extend(payload.get('events') or [])
        total_pages = int(payload.get('total_pages') or 1)
        page += 1
    return records


def make_record(event):
    title = clean_text(event.get('title'))
    url = event.get('url') or ''
    start = event.get('start_date') or ''
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):\d{2}', start)
    venue_data = event.get('venue') or {}
    venue = clean_text(venue_data.get('venue'))
    city = clean_text(venue_data.get('city'))
    country = clean_text(venue_data.get('country')).lower()

    if not title or not url or not match or not venue or not city:
        return None
    if country and country not in {'united states', 'united states of america', 'usa', 'us'}:
        return None
    try:
        event_date = date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None

    description = clean_text(event.get('description')) or clean_text(event.get('excerpt')) or None
    time_from = None if event.get('all_day') else f'{match.group(2)}:{match.group(3)}'
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'US',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class ArtsEmpoweringLifeOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='artsempoweringlife_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
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
            events = get_events(session)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch events API',
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
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    ArtsEmpoweringLifeOrgCrawler().run()


if __name__ == '__main__':
    main()
