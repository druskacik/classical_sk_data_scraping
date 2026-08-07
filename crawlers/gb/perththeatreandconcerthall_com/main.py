import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.perththeatreandconcerthall.com/'
SOURCE = 'Perth Theatre and Concert Hall'
API_URL = 'https://tickets.perththeatreandconcerthall.com/horsecrossarts/api/v3'
TICKETS_URL = 'https://tickets.perththeatreandconcerthall.com/horsecrossarts/website'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
}


def clean_text(value):
    if not value:
        return ''
    text = str(value).replace('\r\n', '\n').replace('\r', '\n').replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, path):
    response = session.get(f'{API_URL}/{path}', timeout=90)
    response.raise_for_status()
    return response.json()


def get_plan_name(session, plan_id):
    """Read only the plan header; full seating plans can be several MB."""
    response = session.get(f'{API_URL}/plans/{plan_id}', timeout=90, stream=True)
    response.raise_for_status()
    prefix = b''
    try:
        for chunk in response.iter_content(chunk_size=4096):
            prefix += chunk
            match = re.search(rb'"name"\s*:\s*"((?:[^"\\]|\\.)*)"', prefix)
            if match:
                # Plan names currently contain no escapes beyond JSON's standard set.
                import json

                return clean_text(json.loads(b'"' + match.group(1) + b'"'))
            if len(prefix) >= 32768:
                break
    finally:
        response.close()
    return ''


def city_for(instance, venue):
    address = clean_text(instance.get('attribute_VenueAddress'))
    location = f'{venue} {address}'.lower()
    if re.search(r'\bperth\b', location):
        return 'Perth'
    return ''


def performance_url(instance):
    # Spektrix's public URLs use the leading numeric part of the opaque API id.
    match = re.match(r'\d+', str(instance.get('id') or ''))
    if not match:
        return ''
    return f'{TICKETS_URL}/ChooseSeats.aspx?EventInstanceID={match.group(0)}'


def make_record(event, instance, venue):
    if instance.get('cancelled'):
        return None
    title = clean_text(event.get('name'))
    url = performance_url(instance)
    city = city_for(instance, venue)
    if not title or not url or not venue or not city:
        return None

    try:
        start = datetime.fromisoformat(instance['start'])
    except (KeyError, TypeError, ValueError):
        return None

    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': url,
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': clean_text(event.get('description')) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = {event['id']: event for event in get_json(session, 'events') if event.get('id')}
    instances = get_json(session, 'instances')

    plan_ids = {instance.get('planId') for instance in instances if instance.get('planId')}
    plan_names = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(get_plan_name, session, plan_id): plan_id for plan_id in plan_ids
        }
        for future in as_completed(futures):
            plan_id = futures[future]
            try:
                plan_names[plan_id] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to resolve performance venue',
                    event='crawler_item_failed',
                    level='warning',
                    url=f'{API_URL}/plans/{plan_id}',
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    records = []
    for instance in instances:
        event = events.get((instance.get('event') or {}).get('id'))
        venue = plan_names.get(instance.get('planId'), '')
        # Spektrix also exposes ticket-protection and membership transactions as
        # instances. They are not performances and have no defensible venue.
        if not event or venue.lower() == 'ticket protection':
            continue
        record = make_record(event, instance, venue)
        if record:
            records.append(record)

    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'], record['title'], record['url']),
    )


class PerthTheatreAndConcertHallCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='perththeatreandconcerthall_com',
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
    PerthTheatreAndConcertHallCrawler().run()


if __name__ == '__main__':
    main()
