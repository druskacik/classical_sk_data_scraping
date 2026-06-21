import re
from datetime import date
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://liedercompany.cz'
EVENTS_API_URL = f'{BASE_URL}/wp-json/tribe/events/v1/events'
SOURCE = 'Lieder Company Prague'
SOURCE_URL = BASE_URL

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def clean_text(text):
    if not text:
        return ''
    text = unescape(str(text)).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def html_to_text(html_content):
    soup = BeautifulSoup(html_content or '', 'html.parser')

    for tag in soup(['script', 'style', 'noscript', 'svg']):
        tag.decompose()

    for selector in [
        '.tribe-events-schedule',
        '.tribe-block__venue',
        '.tribe-block__events-link',
        '.tribe-block__event-price',
        '.wp-block-buttons',
        '.wp-block-image',
        '.tribe-events-c-subscribe-dropdown__container',
    ]:
        for tag in soup.select(selector):
            tag.decompose()

    return clean_text(soup.get_text('\n'))


def parse_date(value):
    if not value:
        return None
    return value.split(' ', 1)[0]


def parse_time(value):
    if not value:
        return None
    match = re.search(r'\b(\d{1,2}:\d{2})\b', value)
    return match.group(1) if match else None


def normalize_city(city):
    city = clean_text(city)
    if not city:
        return None
    if city.startswith('Praha'):
        return 'Praha'
    return city


def venue_name(event):
    venue = event.get('venue')
    if not isinstance(venue, dict):
        return None
    return clean_text(venue.get('venue')) or None


def venue_city(event):
    venue = event.get('venue')
    if not isinstance(venue, dict):
        return None
    return normalize_city(venue.get('city'))


def extract_event(event):
    start_date = event.get('start_date')
    end_date = event.get('end_date')

    concert = {
        'title': clean_text(event.get('title')),
        'date': parse_date(start_date),
        'url': clean_text(event.get('url')) or event.get('website') or SOURCE_URL,
        'time_from': parse_time(start_date),
        'time_to': parse_time(end_date) if end_date and end_date != start_date else None,
        'venue': venue_name(event),
        'city': venue_city(event),
        'description': html_to_text(event.get('description')) or None,
        'type': 'concert',
    }

    if not concert['title'] or not concert['date']:
        return None
    return concert


def fetch_events(session):
    page = 1
    events = []
    end_year = date.today().year + 5

    while True:
        response = session.get(
            EVENTS_API_URL,
            params={
                'page': page,
                'per_page': 50,
                'start_date': '2000-01-01 00:00:00',
                'end_date': f'{end_year}-12-31 23:59:59',
                'status': 'publish',
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        events.extend(data.get('events', []))

        total_pages = int(data.get('total_pages') or 0)
        if page >= total_pages:
            break
        page += 1

    return events


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = []
    for event in fetch_events(session):
        concert = extract_event(event)
        if concert:
            concerts.append(concert)

    return concerts


class LiederCompanyCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='liedercompany_cz',
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
            'description',
            'type',
        ],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    LiederCompanyCrawler().run()


if __name__ == '__main__':
    main()
