import html
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://opera.toulouse.fr/'
EVENTS_API = f'{SOURCE_URL}wp-json/wp/v2/onct-events'
SOURCE = 'Opéra national du Capitole'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9',
}

MONTHS = {
    'janvier': 1,
    'février': 2,
    'fevrier': 2,
    'mars': 3,
    'avril': 4,
    'mai': 5,
    'juin': 6,
    'juillet': 7,
    'août': 8,
    'aout': 8,
    'septembre': 9,
    'octobre': 10,
    'novembre': 11,
    'décembre': 12,
    'decembre': 12,
}

# Venue term IDs used by this Opera site's catalogue. All four are in Toulouse.
# Unrecognised terms are deliberately skipped so touring events do not inherit a
# home venue or city.
VENUES = {
    24: ('Théâtre du Capitole', 'Toulouse'),
    25: ('ThéâtredelaCité', 'Toulouse'),
    26: ('Chapelle des Carmélites', 'Toulouse'),
    27: ('Halle aux Grains', 'Toulouse'),
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
        events.extend(response.json())
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


def parse_occurrences(page_html):
    soup = BeautifulSoup(page_html, 'html.parser')
    occurrences = []
    for month_group in soup.select('#splide-date-event li[data-month]'):
        match = re.fullmatch(r'([A-Za-zÀ-ÿ]+)\s+(\d{4})', month_group.get('data-month', '').strip())
        if not match:
            continue
        month = MONTHS.get(match.group(1).lower())
        year = int(match.group(2))
        if not month:
            continue
        for date_node in month_group.select('.date'):
            day_match = re.search(r'(\d{1,2})\s*$', clean_text(date_node.select_one('.day')))
            if not day_match:
                continue
            try:
                event_date = date(year, month, int(day_match.group(1))).isoformat()
            except ValueError:
                continue
            hour_text = clean_text(date_node.select_one('.hour'))
            time_match = re.search(r'(?<!\d)([01]?\d|2[0-3])[:h]([0-5]\d)', hour_text)
            time_from = (
                f'{int(time_match.group(1)):02d}:{time_match.group(2)}'
                if time_match else None
            )
            occurrences.append((event_date, time_from))
    return occurrences


def fallback_occurrence(event):
    meta = event.get('meta') or {}
    try:
        event_date = date(
            int(meta.get('onct-event-year')),
            int(meta.get('onct-event-month')),
            int(meta.get('onct-event-day')),
        ).isoformat()
    except (TypeError, ValueError):
        return None
    try:
        hour = int(meta.get('onct-event-hour'))
        minute = int(meta.get('onct-event-minute'))
        time_from = f'{hour:02d}:{minute:02d}' if 0 <= hour <= 23 and 0 <= minute <= 59 else None
    except (TypeError, ValueError):
        time_from = None
    return event_date, time_from


def event_records(session, event):
    title = clean_text((event.get('title') or {}).get('rendered'))
    url = event.get('link') or ''
    location_ids = event.get('onct-event-lieu') or []
    location = VENUES.get(location_ids[0]) if len(location_ids) == 1 else None
    if not title or not url or not location:
        return []

    response = session.get(url, timeout=60)
    response.raise_for_status()
    occurrences = parse_occurrences(response.text)
    if not occurrences:
        fallback = fallback_occurrence(event)
        occurrences = [fallback] if fallback else []

    venue, city = location
    description = event_description(event)
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'FR',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date, time_from in occurrences
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        events = listing_events(session)
        records = [record for event in events for record in event_records(session, event)]
    except (requests.RequestException, ValueError) as error:
        log_message(
            'Failed to fetch Opéra national du Capitole catalogue',
            event='crawler_fetch_failed',
            level='error',
            url=EVENTS_API,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        raise
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class OperaToulouseFrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='opera_toulouse_fr',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    OperaToulouseFrCrawler().run()


if __name__ == '__main__':
    main()
