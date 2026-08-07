import html
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://onct.toulouse.fr/'
EVENTS_API = f'{SOURCE_URL}wp-json/wp/v2/onct-events'
SOURCE = 'Orchestre national du Capitole de Toulouse'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'fr-FR,fr;q=0.9',
}

# These are all venue terms currently used by the orchestra's event catalogue.
# Explicit mappings keep addresses and nearby municipalities out of the city
# field and prevent an unknown touring location from receiving a home default.
VENUES = {
    35: ('Halle aux Grains', 'Toulouse'),
    40: ('Place du Capitole', 'Toulouse'),
    41: ("Diagora – Centre de Congrès et d'Exposition", 'Labège'),
    43: ('Basilique Saint-Sernin', 'Toulouse'),
    45: ('ThéâtredelaCité', 'Toulouse'),
    46: ('Zénith Toulouse Métropole', 'Toulouse'),
    49: ('Auditorium Saint-Pierre des Cuisines', 'Toulouse'),
    781: ('Halle de la Machine', 'Toulouse'),
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(html.unescape(str(value)), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def listing_events(session):
    events = []
    page = 1
    while True:
        response = session.get(
            EVENTS_API,
            params={
                'per_page': 100,
                'page': page,
                'status': 'publish',
                '_fields': 'id,link,title,content,meta,onct-event-lieu',
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        events.extend(payload)
        total_pages = int(response.headers.get('X-WP-TotalPages', page))
        if page >= total_pages:
            return events
        page += 1


def event_description(event):
    meta = event.get('meta') or {}
    parts = []
    for value in (
        meta.get('onct-event-short-desc'),
        meta.get('onct-event-desc'),
        (event.get('content') or {}).get('rendered'),
    ):
        text = clean_text(value)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def make_record(event):
    meta = event.get('meta') or {}
    title = clean_text((event.get('title') or {}).get('rendered'))
    url = event.get('link') or ''

    try:
        event_date = date(
            int(meta.get('onct-event-year')),
            int(meta.get('onct-event-month')),
            int(meta.get('onct-event-day')),
        ).isoformat()
    except (TypeError, ValueError):
        return None

    location_ids = event.get('onct-event-lieu') or []
    location = VENUES.get(location_ids[0]) if len(location_ids) == 1 else None
    if not title or not url or not location:
        return None

    try:
        hour = int(meta.get('onct-event-hour'))
        minute = int(meta.get('onct-event-minute'))
        time_from = f'{hour:02d}:{minute:02d}' if 0 <= hour <= 23 and 0 <= minute <= 59 else None
    except (TypeError, ValueError):
        time_from = None

    venue, city = location
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'FR',
        'description': event_description(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        events = listing_events(session)
    except (requests.RequestException, ValueError) as error:
        log_message(
            'Failed to fetch ONCT concert catalogue',
            event='crawler_fetch_failed',
            level='error',
            url=EVENTS_API,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        raise

    records = [record for event in events if (record := make_record(event))]
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class OnctToulouseFrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='onct_toulouse_fr',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
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
    OnctToulouseFrCrawler().run()


if __name__ == '__main__':
    main()
