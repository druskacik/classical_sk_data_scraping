import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.staatstheater-kassel.de/'
API_URL = 'https://backend.staatstheater-kassel.de/'
SOURCE = 'Staatstheater Kassel'
CITY = 'Kassel'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}

INVALID_LOCATIONS = {'', 'mobil', 'ort s. unten sch'}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, path, params=None):
    response = session.get(urljoin(API_URL, path), params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def published_seasons(session):
    seasons = get_json(session, 'seasons')
    names = []
    for item in seasons:
        name = clean_text(item.get('Name') or (item.get('Season') or {}).get('Name'))
        if name and name not in names:
            names.append(name)
    return names


def schedule_events(session):
    events = []
    seen_ids = set()
    for season in published_seasons(session):
        for event in get_json(session, 'search/schedule', params={'season': season}):
            event_id = event.get('id')
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            events.append(event)
    return events


def production_url(slug):
    return urljoin(SOURCE_URL, f'play/{slug}') if slug else ''


def resolve_venue(event, detail):
    venue = clean_text(event.get('Location') or detail.get('Location'))
    if venue.lower() not in INVALID_LOCATIONS:
        return venue

    description = clean_text(detail.get('Description'))
    if re.search(r'\bHessenkampfbahn\b', description, re.IGNORECASE):
        return 'Hessenkampfbahn'
    if re.search(r'\bOttoneum\b', description, re.IGNORECASE):
        return 'Ottoneum / Naturkundemuseum'
    return None


def description_text(event, detail):
    parts = []
    for value in (
        detail.get('Description'),
        detail.get('Description_2'),
        event.get('Description'),
        event.get('Introduction'),
    ):
        text = clean_text(value)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def make_record(event, detail=None):
    detail = detail or {}
    title = clean_text(event.get('Title') or detail.get('Title'))
    subtitle = clean_text(event.get('Subtitle') or detail.get('Subtitle'))
    if subtitle and subtitle.lower() not in title.lower():
        title = f'{title} – {subtitle}'

    slug = clean_text(event.get('PlaySlug') or detail.get('Slug'))
    url = production_url(slug)
    venue = resolve_venue(event, detail)
    start = clean_text(event.get('StartDate'))
    try:
        start_at = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None

    if event.get('Canceled') or not title or not url or not venue:
        return None

    return {
        'title': title,
        'date': start_at.date().isoformat(),
        'url': url,
        'time_from': start_at.strftime('%H:%M'),
        'venue': venue,
        'city': CITY,
        'country_code': 'DE',
        'description': description_text(event, detail),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = schedule_events(session)
    details = {}
    slugs = {clean_text(event.get('PlaySlug')) for event in events}
    slugs.discard('')

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(get_json, session, f'play/{slug}'): slug
            for slug in slugs
        }
        for future in as_completed(futures):
            slug = futures[future]
            try:
                details[slug] = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape production detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=production_url(slug),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    records = []
    for event in events:
        slug = clean_text(event.get('PlaySlug'))
        record = make_record(event, details.get(slug))
        if record:
            records.append(record)

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class StaatstheaterKasselDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='staatstheater_kassel_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
    StaatstheaterKasselDeCrawler().run()


if __name__ == '__main__':
    main()
