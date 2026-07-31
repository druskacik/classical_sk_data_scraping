import re
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.cfsbrno.cz'
SOURCE_URL = f'{BASE_URL}/'
CALENDAR_URL = f'{BASE_URL}/calendar.ashx'
SOURCE = 'Český filharmonický sbor Brno'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Referer': SOURCE_URL,
    'X-Requested-With': 'XMLHttpRequest',
}


def clean_text(value):
    if not value:
        return None
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() or None


def html_to_text(value):
    if not value:
        return None
    soup = BeautifulSoup(value, 'html.parser')
    for unwanted in soup.select('script, style, img'):
        unwanted.decompose()
    return clean_text(soup.get_text('\n', strip=True))


def parse_title_html(value):
    soup = BeautifulSoup(value or '', 'html.parser')
    content = soup.select_one('.eventContent') or soup
    location_element = content.select_one('strong')
    location = clean_text(location_element.get_text(' ', strip=True)) if location_element else None
    if location_element:
        location_element.decompose()
    title = clean_text(content.get_text(' ', strip=True))
    return title, location


def parse_location(value):
    if not value:
        return None, None, None

    parts = [clean_text(part) for part in value.split(',')]
    parts = [part for part in parts if part]
    if not parts:
        return None, None, None

    time_from = None
    time_match = re.fullmatch(r'(\d{1,2})[.:](\d{2})', parts[-1])
    if time_match:
        time_from = f'{int(time_match.group(1)):02d}:{time_match.group(2)}'
        parts.pop()

    city = re.sub(r'\s*\([^)]*\)\s*$', '', parts[0]).strip() or None
    venue = clean_text(', '.join(parts[1:]))
    return city, venue, time_from


def fetch_events(session):
    today = date.today()
    response = session.post(
        CALENDAR_URL,
        data={
            'lang': 'cz',
            'start': today.isoformat(),
            # The endpoint accepts arbitrary ranges, so this is independent of
            # the month currently displayed by the website's calendar.
            'end': (today + timedelta(days=730)).isoformat(),
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError('Calendar API returned an unexpected response')
    return payload


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    concerts = []

    for event in fetch_events(session):
        event_id = event.get('id')
        event_date = clean_text(event.get('start'))
        title, location = parse_title_html(event.get('title'))
        if not title or not event_date:
            continue

        city, venue, time_from = parse_location(location)
        description = html_to_text(event.get('Description'))
        if location:
            description = clean_text(
                f'Místo a čas: {location}\n\n{description or ""}'
            )

        url = f'{SOURCE_URL}#event-{event_id}' if event_id is not None else SOURCE_URL
        concerts.append({
            'title': title,
            'date': event_date[:10],
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'CZ',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })

    return concerts


class CfsBrnoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cfsbrno_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
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
        dedupe_subset=['title', 'date', 'time_from', 'url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    CfsBrnoCrawler().run()


if __name__ == '__main__':
    main()
