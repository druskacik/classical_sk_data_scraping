import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.brittenpearsarts.org/'
SOURCE = 'Britten Pears Arts'
API_URL = 'https://system.spektrix.com/snapemaltings/api/v3'
BOOKING_URL = 'https://system.spektrix.com/snapemaltings/website/EventDetails.aspx?EventId={}'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
}

MUSIC_ATTRIBUTES = (
    'attribute_Classical',
    'attribute_Contemporary',
    'attribute_FolkRootsAndWorld',
    'attribute_Jazz',
    'attribute_Opera',
    'attribute_PopAndRock',
    'attribute_PopularClassics',
)


def clean_text(value):
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
    session.mount(
        'https://',
        HTTPAdapter(
            pool_connections=12,
            pool_maxsize=12,
            max_retries=Retry(
                total=3,
                backoff_factor=0.7,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=('GET',),
            ),
        ),
    )
    return session


def get_json(session, path):
    response = session.get(f'{API_URL}/{path}', timeout=60)
    response.raise_for_status()
    return response.json()


def is_music_event(event):
    if event.get('attribute_HideFromWebsiteListing'):
        return False
    first_tier = clean_text(event.get('attribute_FirstTierArtform')).casefold()
    event_type = clean_text(event.get('attribute_EventType')).casefold()
    return (
        first_tier == 'music'
        or event_type == 'music'
        or any(event.get(attribute) is True for attribute in MUSIC_ATTRIBUTES)
    )


def city_from_address(address, venue):
    text = f'{address} {venue}'.casefold()
    # Events take place around Suffolk as well as at the organisation's two
    # home sites, so infer a city only from the particular venue/address.
    places = (
        ('bury st edmunds', 'Bury St Edmunds'),
        ('great yarmouth', 'Great Yarmouth'),
        ('southwold', 'Southwold'),
        ('blythburgh', 'Blythburgh'),
        ('framlingham', 'Framlingham'),
        ('leiston', 'Leiston'),
        ('orford', 'Orford'),
        ('aldeburgh', 'Aldeburgh'),
        ('snape', 'Snape'),
        ('saxmundham', 'Saxmundham'),
        ('ipswich', 'Ipswich'),
        ('woodbridge', 'Woodbridge'),
        ('lowestoft', 'Lowestoft'),
    )
    for marker, city in places:
        if marker in text:
            return city
    return None


def fetch_resources(session, resource, ids):
    values = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(get_json, session, f'{resource}/{item_id}'): item_id
            for item_id in ids
        }
        for future in as_completed(futures):
            item_id = futures[future]
            try:
                values[item_id] = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to fetch Britten Pears Arts API resource',
                    event='crawler_item_failed',
                    level='warning',
                    url=f'{API_URL}/{resource}/{item_id}',
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return values


def event_description(event):
    description = clean_text(event.get('htmlDescription'))
    return description or clean_text(event.get('description')) or None


def get_concerts():
    session = make_session()
    events = get_json(session, 'events')
    instances = get_json(session, 'instances')
    events_by_id = {
        event['id']: event
        for event in events
        if event.get('id') and is_music_event(event)
    }

    relevant_instances = [
        instance
        for instance in instances
        if instance.get('event', {}).get('id') in events_by_id
        and not instance.get('cancelled')
        and instance.get('start')
        and instance.get('planId')
    ]
    plan_ids = {instance['planId'] for instance in relevant_instances}
    plans = fetch_resources(session, 'plans', plan_ids)
    venue_ids = {
        plan.get('venue', {}).get('id')
        for plan in plans.values()
        if plan.get('venue', {}).get('id')
    }
    venues = fetch_resources(session, 'venues', venue_ids)

    records = []
    for instance in relevant_instances:
        event_id = instance['event']['id']
        event = events_by_id[event_id]
        plan = plans.get(instance['planId'])
        if not plan:
            continue
        venue_info = venues.get(plan.get('venue', {}).get('id'), {})
        venue = clean_text(plan.get('name')) or clean_text(venue_info.get('name'))
        address = clean_text(venue_info.get('address'))
        city = city_from_address(address, venue)
        title = clean_text(event.get('name'))
        try:
            starts_at = datetime.fromisoformat(instance['start'])
        except (TypeError, ValueError):
            continue
        if not title or not venue or not city:
            continue
        records.append({
            'title': title,
            'date': starts_at.date().isoformat(),
            'url': BOOKING_URL.format(event_id),
            'time_from': starts_at.strftime('%H:%M'),
            'venue': venue,
            'city': city,
            'country_code': 'GB',
            'description': event_description(event),
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class BrittenPearsArtsOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='brittenpearsarts_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    BrittenPearsArtsOrgCrawler().run()


if __name__ == '__main__':
    main()
