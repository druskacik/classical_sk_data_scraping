import html
import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.bachconsort.com/'
EVENTS_URL = urljoin(SOURCE_URL, 'events')
SOURCE = 'Bach Consort Wien'
TIMEZONE = ZoneInfo('Europe/Vienna')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

COUNTRY_CODES = {
    'argentina': 'AR',
    'austria': 'AT',
    'österreich': 'AT',
    'croatia': 'HR',
    'deutschland': 'DE',
    'germany': 'DE',
    'kroatien': 'HR',
    'poland': 'PL',
    'polen': 'PL',
    'schweiz': 'CH',
    'spain': 'ES',
    'spanien': 'ES',
    'switzerland': 'CH',
}


def clean_text(value):
    if not value:
        return ''
    text = html.unescape(str(value)).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def description_from_html(value):
    if not value:
        return None
    soup = BeautifulSoup(value, 'html.parser')
    for element in soup.select(
        'script, style, figure, .sqs-block-button-container, .eventitem-meta'
    ):
        element.decompose()
    return clean_text(soup.get_text('\n', strip=True)) or None


def city_from_location(location):
    line = clean_text(location.get('addressLine2'))
    if not line:
        return ''
    parts = [part.strip() for part in line.split(',') if part.strip()]
    if not parts:
        return ''
    # Squarespace commonly emits either "Wien, Wien, 1010" or
    # "1010 Wien". Remove a postal code without losing the locality.
    candidate = parts[0]
    candidate = re.sub(r'^\d{4,6}\s+', '', candidate).strip()
    candidate = re.sub(r'\s+\d{4,6}$', '', candidate).strip()
    return candidate


def country_code_from_location(location):
    country = clean_text(location.get('addressCountry')).lower()
    return COUNTRY_CODES.get(country, '')


def parse_event(item):
    title = clean_text(item.get('title'))
    url_id = clean_text(item.get('urlId')).lstrip('/')
    location = item.get('location') or {}
    venue = clean_text(location.get('addressTitle'))
    city = city_from_location(location)
    country_code = country_code_from_location(location)
    start_ms = item.get('startDate')
    if not title or not url_id or not venue or not city or not country_code or not start_ms:
        return None

    try:
        start = datetime.fromtimestamp(float(start_ms) / 1000, TIMEZONE)
    except (TypeError, ValueError, OSError):
        return None

    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': urljoin(EVENTS_URL + '/', url_id),
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description_from_html(item.get('body')),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def fetch_all_events(session):
    url = EVENTS_URL
    params = {'format': 'json'}
    seen_offsets = set()
    items = []

    while url:
        response = session.get(url, params=params, timeout=45)
        response.raise_for_status()
        payload = response.json()
        items.extend(payload.get('upcoming') or [])
        items.extend(payload.get('past') or [])

        pagination = payload.get('pagination') or {}
        next_url = pagination.get('nextPageUrl') if pagination.get('nextPage') else None
        offset = pagination.get('nextPageOffset')
        if not next_url or offset in seen_offsets:
            break
        seen_offsets.add(offset)
        url = urljoin(SOURCE_URL, next_url)
        params = {'format': 'json'}

    return items


class BachconsortComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bachconsort_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        records = []
        for item in fetch_all_events(session):
            record = parse_event(item)
            if record:
                records.append(record)
            else:
                log_message(
                    'Skipped incomplete Bach Consort Wien event',
                    event='crawler_item_skipped',
                    level='warning',
                    url=urljoin(EVENTS_URL + '/', clean_text(item.get('urlId')).lstrip('/')),
                    error_type='IncompleteEventData',
                    error_message='Required date, title, venue, city, or country is missing',
                )
        return sorted(
            records,
            key=lambda item: (
                item['date'], item['time_from'] or '', item['title'], item['venue']
            ),
        )


def main():
    BachconsortComCrawler().run()


if __name__ == '__main__':
    main()
