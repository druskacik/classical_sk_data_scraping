import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.kingsplace.co.uk/'
SOURCE = 'Kings Place'
CITY = 'London'
API_URL = 'https://system.spektrix.com/kingsplace/api/v3'
NON_EVENT_TITLES = {'rotunda restaurant', 'secure my booking'}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-GB,en;q=0.9',
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


def parse_start(value):
    try:
        start = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return start.date().isoformat(), start.strftime('%H:%M')


def event_description(event):
    parts = []
    for field in ('description', 'htmlDescription'):
        text = clean_text(event.get(field))
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def plan_names(session, instances):
    names = {}
    for plan_id in sorted({instance.get('planId') for instance in instances if instance.get('planId')}):
        url = f'{API_URL}/plans/{plan_id}'
        try:
            plan = get_json(session, url)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch Kings Place plan',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        name = clean_text(plan.get('name'))
        if name:
            names[plan_id] = name
    return names


def records_from_payloads(events, instances, plans):
    events_by_id = {event.get('id'): event for event in events if event.get('id')}
    records = []
    seen = set()

    for instance in instances:
        event = events_by_id.get((instance.get('event') or {}).get('id'))
        if not event or instance.get('cancelled'):
            continue

        title = clean_text(event.get('name'))
        venue = plans.get(instance.get('planId'), '')
        performance = parse_start(instance.get('start'))
        if not title or title.casefold() in NON_EVENT_TITLES or not venue or not performance:
            continue

        event_date, time_from = performance
        event_id = event['id']
        url = f'{API_URL}/events/{event_id}'
        key = (event_id, event_date, time_from, venue)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': CITY,
                'country_code': 'GB',
                'description': event_description(event),
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = get_json(session, f'{API_URL}/events')
    instances = get_json(session, f'{API_URL}/instances')
    plans = plan_names(session, instances)
    return records_from_payloads(events, instances, plans)


class KingsPlaceCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='kingsplace_co_uk',
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
        return get_concerts()


def main():
    KingsPlaceCoUkCrawler().run()


if __name__ == '__main__':
    main()
