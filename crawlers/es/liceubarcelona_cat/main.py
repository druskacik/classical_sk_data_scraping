import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.liceubarcelona.cat/ca'
PROGRAMME_API = 'https://www.liceubarcelona.cat/sites/default/files/programme.json'
SOURCE = 'Gran Teatre del Liceu'
DEFAULT_VENUE = 'Gran Teatre del Liceu'
DEFAULT_CITY = 'Barcelona'
LOCAL_TIMEZONE = ZoneInfo('Europe/Madrid')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'ca-ES,ca;q=0.9,es;q=0.8',
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def localized(value):
    if isinstance(value, dict):
        return value.get('ca') or value.get('es') or value.get('en') or ''
    return value or ''


def get_json(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def event_schema(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or '')
        except (TypeError, json.JSONDecodeError):
            continue
        nodes = payload.get('@graph', []) if isinstance(payload, dict) else []
        for node in nodes:
            if node.get('@type') in ('Event', 'MusicEvent', 'TheaterEvent'):
                return node
    return {}


def parse_detail(response):
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    schema = event_schema(soup)
    location = schema.get('location') or {}
    address = location.get('address') or {}
    venue = clean_text(location.get('name')) or DEFAULT_VENUE
    city = clean_text(address.get('addressLocality')) or DEFAULT_CITY

    # This field contains the editorial synopsis and programme discussion while
    # excluding ticket controls and repeated session listings.
    description = clean_text(soup.select_one('.field--name-field-main-content'))
    if not description:
        description = clean_text(schema.get('description'))
    return venue, city, description or None


def production_url(production):
    path = localized(production.get('url'))
    return urljoin(SOURCE_URL, path) if path else ''


def fallback_description(production):
    parts = []
    subtitle = clean_text(localized(production.get('subtitle')))
    if subtitle:
        parts.append(subtitle)
    composer = production.get('composer') or {}
    composer_name = clean_text(localized(composer.get('name')))
    if composer_name and composer_name not in parts:
        parts.append(f'Compositor: {composer_name}')
    return '\n'.join(parts) or None


def make_records(production, detail):
    title = clean_text(localized(production.get('title')))
    url = production_url(production)
    venue, city, description = detail
    description = description or fallback_description(production)
    if not title or not url or not venue or not city:
        return []

    records = []
    for session in production.get('sessions') or []:
        timestamp = session.get('date')
        try:
            start = datetime.fromtimestamp(int(timestamp), LOCAL_TIMEZONE)
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        records.append({
            'title': title,
            'date': start.date().isoformat(),
            'url': url,
            'time_from': start.strftime('%H:%M'),
            'venue': venue,
            'city': city,
            'country_code': 'ES',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    productions = list((get_json(session, PROGRAMME_API).get('productions') or {}).values())
    details = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(session.get, url, timeout=45): production
            for production in productions
            if (url := production_url(production))
        }
        for future in as_completed(futures):
            production = futures[future]
            url = production_url(production)
            try:
                details[production['id']] = parse_detail(future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape production detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    records = []
    default_detail = (DEFAULT_VENUE, DEFAULT_CITY, None)
    for production in productions:
        records.extend(make_records(production, details.get(production.get('id'), default_detail)))
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class LiceubarcelonaCatCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='liceubarcelona_cat',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='ES',
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
    LiceubarcelonaCatCrawler().run()


if __name__ == '__main__':
    main()
