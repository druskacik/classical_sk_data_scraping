import math
import re
from datetime import date
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://helsinkifestival.fi/'
EVENTS_API_URL = f'{SOURCE_URL}wp-json/events/v1/search'
SOURCE = 'Helsinki Festival'
HOME_CITY = 'Helsinki'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'fi-FI,fi;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = str(value)
    if '<' in text:
        text = BeautifulSoup(text, 'html.parser').get_text('\n', strip=True)
    else:
        text = unescape(text)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_page(session, page):
    response = session.get(
        EVENTS_API_URL,
        params={'language': 'fi', 'page': page, 'view': 'list'},
        timeout=45,
    )
    response.raise_for_status()
    return response.json()


def get_events(session):
    first_page = get_page(session, 1)
    events = list(first_page.get('posts') or [])
    total = int(first_page.get('total') or len(events))
    page_size = len(events)
    if not page_size:
        return []

    for page in range(2, math.ceil(total / page_size) + 1):
        payload = get_page(session, page)
        events.extend(payload.get('posts') or [])
    return events


def resolve_location(event):
    location = clean_text(event.get('location'))
    geocoded = event.get('location2') or {}
    geocoded_name = clean_text(geocoded.get('name'))
    city = clean_text(geocoded.get('city'))
    country_code = clean_text(geocoded.get('country_short')).upper()

    venue = location or geocoded_name
    if not venue:
        return None, None, None

    # Festival events are based in Helsinki, but retain explicit API geography
    # for any exceptional event outside the city rather than applying defaults.
    city = city or HOME_CITY
    country_code = country_code or 'FI'
    if not re.fullmatch(r'[A-Z]{2}', country_code):
        return None, None, None
    return venue, city, country_code


def parse_show(show):
    raw_date = clean_text(show.get('date'))
    try:
        event_date = date.fromisoformat(raw_date).isoformat()
    except (TypeError, ValueError):
        return None, None

    raw_time = clean_text(show.get('time'))
    match = re.fullmatch(r'(\d{1,2}):(\d{2})', raw_time)
    if not match:
        return event_date, None
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        return event_date, None
    return event_date, f'{hour:02d}:{minute:02d}'


def event_description(event):
    content = event.get('content')
    if not content:
        translations = event.get('debug_group_content') or {}
        content = translations.get('fi')
    return clean_text(content) or None


def make_records(event):
    title = clean_text(event.get('name'))
    subtitle = clean_text(event.get('subtitle'))
    if subtitle and subtitle.lower() not in title.lower():
        title = f'{title} – {subtitle}'

    url = clean_text(event.get('link'))
    venue, city, country_code = resolve_location(event)
    if not title or not url or not venue or not city or not country_code:
        return []

    description = event_description(event)
    records = []
    for show in event.get('shows') or []:
        event_date, time_from = parse_show(show)
        if not event_date:
            continue
        records.append(
            {
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': country_code,
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = get_events(session)
    records = []
    for event in events:
        try:
            records.extend(make_records(event))
        except (TypeError, ValueError) as error:
            log_message(
                'Failed to parse Helsinki Festival event',
                event='crawler_item_failed',
                level='warning',
                url=clean_text(event.get('link')),
                error_type=type(error).__name__,
                error_message=str(error),
            )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class HelsinkiFestivalFiCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='helsinkifestival_fi',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FI',
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
    HelsinkiFestivalFiCrawler().run()


if __name__ == '__main__':
    main()
