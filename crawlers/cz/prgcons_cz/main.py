import re
from datetime import datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://prazskakonzervator.cz'
SOURCE_URL = 'https://www.prgcons.cz/'
SOURCE = 'Pražská konzervatoř'
EVENTS_API_URL = f'{BASE_URL}/wp-json/tribe/events/v1/events'
SCHOOL_CONCERTS_CATEGORY_ID = 182

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) HeadlessChrome/149.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json,text/html;q=0.9,*/*;q=0.8',
    'Accept-Language': 'cs-CZ,cs;q=0.9,en-US;q=0.8,en;q=0.7',
    'Upgrade-Insecure-Requests': '1',
    'Sec-CH-UA': '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    'Sec-CH-UA-Mobile': '?0',
    'Sec-CH-UA-Platform': '"Linux"',
}


def clean_text(text):
    if not text:
        return ''

    text = unescape(str(text))
    if '<' in text and '>' in text:
        text = BeautifulSoup(text, 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_datetime(value):
    if not value:
        return None

    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def normalize_city(value):
    value = clean_text(value)
    if not value:
        return 'Praha'

    if re.search(r'\bPraha\b', value, re.IGNORECASE):
        return 'Praha'

    return value


def venue_name(venue):
    if not isinstance(venue, dict):
        return None

    return clean_text(venue.get('venue')) or None


def venue_address(venue):
    if not isinstance(venue, dict):
        return None

    parts = [
        clean_text(venue.get('address')),
        clean_text(venue.get('city')),
        clean_text(venue.get('zip')),
    ]
    return clean_text(', '.join(part for part in parts if part)) or None


def event_category_ids(event):
    ids = set()
    for category in event.get('categories') or []:
        if not isinstance(category, dict):
            continue
        for key in ('id', 'parent', 'term_taxonomy_id'):
            value = category.get(key)
            if isinstance(value, int):
                ids.add(value)
    return ids


def is_school_concert(event):
    return SCHOOL_CONCERTS_CATEGORY_ID in event_category_ids(event)


def event_type(event):
    categories = [
        clean_text(category.get('name'))
        for category in event.get('categories') or []
        if isinstance(category, dict) and clean_text(category.get('name'))
    ]
    return ', '.join(categories) or 'concert'


def fetch_events(session):
    events = []
    page = 1

    while True:
        response = session.get(
            EVENTS_API_URL,
            params={'per_page': 50, 'page': page},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        events.extend(data.get('events', []))

        total_pages = int(data.get('total_pages') or 1)
        if page >= total_pages:
            break
        page += 1

    return [event for event in events if is_school_concert(event)]


def detail_text(session, url):
    if not url:
        return None

    response = session.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    for tag in soup.select('script, style, iframe, form, nav, .tribe-events-back'):
        tag.decompose()

    parts = []
    for selector in (
        '.tribe-events-single-event-description',
        '.tribe-events-content',
        '.tribe-events-event-meta',
    ):
        element = soup.select_one(selector)
        text = clean_text(element.get_text('\n', strip=True)) if element else ''
        if text and text not in parts:
            parts.append(text)

    return clean_text('\n\n'.join(parts)) or None


def build_description(event, detail_description, date, time_from, venue, address, category):
    parts = [
        clean_text(event.get('title')),
        f'{date} {time_from}' if date and time_from else date,
        category,
        venue,
        address,
        clean_text(event.get('description')),
        clean_text(event.get('excerpt')),
        detail_description,
    ]
    return clean_text('\n\n'.join(part for part in parts if part)) or None


def parse_event(session, event):
    title = clean_text(event.get('title'))
    start = parse_datetime(event.get('start_date'))
    end = parse_datetime(event.get('end_date'))
    url = clean_text(event.get('url'))
    venue = event.get('venue') if isinstance(event.get('venue'), dict) else {}

    if not title or not start or not url:
        return None

    concert_venue = venue_name(venue) or SOURCE
    address = venue_address(venue)
    category = event_type(event)
    detail_description = detail_text(session, url)

    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': url,
        'time_from': None if event.get('all_day') else start.strftime('%H:%M'),
        'time_to': None if event.get('all_day') or not end else end.strftime('%H:%M'),
        'venue': concert_venue,
        'city': normalize_city(venue.get('city')),
        'type': category,
        'description': build_description(
            event,
            detail_description,
            start.date().isoformat(),
            None if event.get('all_day') else start.strftime('%H:%M'),
            concert_venue,
            address,
            category,
        ),
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = []
    for event in fetch_events(session):
        try:
            concert = parse_event(session, event)
        except requests.RequestException as exc:
            print(f'Failed to scrape {event.get("url")}: {exc}')
            continue

        if concert:
            concerts.append(concert)

    return sorted(concerts, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class PrgconsCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='prgcons_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'time_to',
            'venue',
            'city',
            'type',
            'description',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    PrgconsCrawler().run()


if __name__ == '__main__':
    main()
