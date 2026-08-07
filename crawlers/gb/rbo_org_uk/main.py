from datetime import datetime
import re

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.rbo.org.uk/'
SOURCE = 'Royal Ballet and Opera'
EVENTS_API_URL = f'{SOURCE_URL}api/events'
CITY = 'London'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-GB,en;q=0.9',
}

# API location 0 is the building as a whole. Other London locations are
# rooms/stages within it; spelling them out makes the venue independently useful.
VENUES = {
    '0': 'Royal Opera House',
    '2': 'Royal Opera House – Main Stage',
    '5': 'Royal Opera House – Crush Room',
    '73': 'Royal Opera House – Linbury Theatre',
    '76': 'Royal Opera House – Clore Studio',
    '31': 'Bob and Tamar Manoukian Production Workshop and Costume Centre',
}


def clean_html(value):
    if not value:
        return ''
    text = BeautifulSoup(value, 'html.parser').get_text('\n', strip=True)
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
            max_retries=Retry(
                total=3,
                backoff_factor=0.7,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=('GET',),
            )
        ),
    )
    return session


def event_venue(event):
    locations = event.get('relationships', {}).get('locations', {}).get('data', [])
    location_ids = [str(location.get('id')) for location in locations]
    # Prefer the specific room over the building-level relationship.
    for location_id in location_ids:
        if location_id != '0' and location_id in VENUES:
            return VENUES[location_id], ('Purfleet' if location_id == '31' else CITY)
    if '0' in location_ids:
        return VENUES['0'], CITY
    return None, None


def parse_event(event):
    attributes = event.get('attributes') or {}
    title = clean_html(attributes.get('title'))
    slug = attributes.get('slug')
    venue, city = event_venue(event)
    if not title or not slug or not venue or not city:
        return []

    description_parts = []
    for field in ('description', 'carouselDescription'):
        text = clean_html(attributes.get(field))
        if text and text not in description_parts:
            description_parts.append(text)
    description = '\n\n'.join(description_parts) or None
    url = f'{SOURCE_URL}tickets-and-events/{slug}-details'
    records = []

    for performance in attributes.get('performances') or []:
        value = performance.get('date')
        try:
            timestamp = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            continue
        records.append({
            'title': title,
            'date': timestamp.date().isoformat(),
            'url': url,
            'time_from': timestamp.strftime('%H:%M'),
            'venue': venue,
            'city': city,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    response = make_session().get(EVENTS_API_URL, timeout=60)
    response.raise_for_status()
    payload = response.json()
    events = payload.get('data') or []
    records = []

    for event in events:
        try:
            records.extend(parse_event(event))
        except (AttributeError, TypeError, ValueError) as error:
            log_message(
                'Failed to parse Royal Ballet and Opera event',
                event='crawler_item_failed',
                level='warning',
                url=EVENTS_API_URL,
                event_id=event.get('id') if isinstance(event, dict) else None,
                error_type=type(error).__name__,
                error_message=str(error),
            )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class RboOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='rbo_org_uk',
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
    RboOrgUkCrawler().run()


if __name__ == '__main__':
    main()
