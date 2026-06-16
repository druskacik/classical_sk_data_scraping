import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ..base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.hamu.cz'
EVENTS_API_URL = f'{BASE_URL}/api/events/'
SOURCE_NAME = 'HAMU'
SOURCE_URL = BASE_URL
PRAGUE_TZ = ZoneInfo('Europe/Prague')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json,text/html;q=0.9,*/*;q=0.8',
}


def clean_text(text):
    if not text:
        return ''
    text = text.replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(PRAGUE_TZ)


def is_concert(event):
    categories = event.get('categories') or ''
    return any(category.strip().lower() in {'koncert', 'concert'} for category in categories.split(','))


def get_event_page(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def extract_detail_data(session, url):
    soup = get_event_page(session, url)

    description_el = soup.select_one('.event-detail__text')
    description = clean_text(description_el.get_text('\n', strip=True)) if description_el else ''

    map_el = soup.select_one('.event-detail__map--text')
    venue = None
    city = 'Praha'

    if map_el:
        venue_el = map_el.find('h3')
        venue = clean_text(venue_el.get_text(' ', strip=True)) if venue_el else None
        address = clean_text(map_el.get_text('\n', strip=True))
        if address:
            description = clean_text(f'{description}\n\n{address}' if description else address)
            if re.search(r'\bBrno\b', address, re.IGNORECASE):
                city = 'Brno'
            elif re.search(r'\bPraha\b', address, re.IGNORECASE):
                city = 'Praha'

    return {
        'description': description or None,
        'venue': venue,
        'city': city,
    }


def get_events(session):
    events = []
    page = 1

    while page:
        response = session.get(
            EVENTS_API_URL,
            params={'page': page, 'upcoming': 'true', 'count': 100},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        events.extend(data.get('items', []))
        page = data.get('next_page')

    return [event for event in events if is_concert(event)]


def extract_concert(session, event):
    start = parse_datetime(event.get('start'))
    end = parse_datetime(event.get('end'))
    url = urljoin(BASE_URL, event['url'])
    detail_data = extract_detail_data(session, url)

    return {
        'title': clean_text(event.get('title')),
        'date': start.date().isoformat() if start else None,
        'time_from': start.strftime('%H:%M') if start else None,
        'time_to': end.strftime('%H:%M') if end else None,
        'url': url,
        'venue': detail_data['venue'] or 'HAMU',
        'city': detail_data['city'],
        'type': clean_text(event.get('categories')),
        'description': detail_data['description'],
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    events = get_events(session)
    concerts = []
    for event in events:
        try:
            concerts.append(extract_concert(session, event))
        except requests.RequestException as exc:
            print(f'Failed to scrape {event.get("url")}: {exc}')

    return concerts


class HamuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='hamu_cz',
        source=SOURCE_NAME,
        source_url=SOURCE_URL,
        columns=['title', 'date', 'time_from', 'time_to', 'url', 'venue', 'city', 'type', 'description'],
        dedupe_subset=['title', 'date', 'time_from'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    HamuCrawler().run()


if __name__ == '__main__':
    main()
