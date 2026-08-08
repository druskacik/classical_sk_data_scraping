import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.tonkuenstler.at/en'
CONCERTS_URL = f'{SOURCE_URL}/concerts'
EVENTS_API = f'{SOURCE_URL}/api/events/'
SOURCE = 'Tonkünstler-Orchester'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-GB,en;q=0.9,de-AT;q=0.8',
}

GERMAN_CITIES = {'München', 'Nürnberg', 'Passau'}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url, params=None):
    response = session.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response.json()


def listing_events(session):
    # The API requires a date. An old date is intentionally used because the
    # server then returns every concert it still publishes, not just concerts
    # after the crawler's run date.
    url = EVENTS_API
    params = {'date': '2000-01-01T00:00:00.000Z', 'page_size': 100}
    events = []
    while url:
        payload = get_json(session, url, params=params)
        events.extend(payload.get('results') or [])
        url = payload.get('next')
        params = None
    return events


def localized_slug(event):
    slug = event.get('slug') or {}
    return slug.get('en') if isinstance(slug, dict) else slug


def event_url(event):
    slug = localized_slug(event)
    event_id = event.get('id')
    if not slug or not event_id:
        return ''
    return f'{CONCERTS_URL}/{slug}/{event_id}'


def resolve_location(event):
    room = event.get('room') or {}
    venue_data = room.get('venue') or {}
    venue = clean_text(room.get('name'))
    venue_label = clean_text(venue_data.get('name')).strip(' |')
    address = clean_text(venue_data.get('address'))

    city = venue_label
    if not city and address:
        match = re.search(r'\b\d{4,5}\s+([^,]+)$', address)
        city = clean_text(match.group(1)) if match else ''
    if not city and ' | ' in venue:
        city = clean_text(venue.split(' | ', 1)[0])

    # Some venue labels name the building rather than its city.
    if city == 'Festspielhaus St. Pölten':
        city = 'St. Pölten'
    elif city == 'Congress Innsbruck':
        city = 'Innsbruck'
    elif city == 'Arena Wien':
        city = 'Wien'
    elif city == 'Tischlerei Melk Kulturwerkstatt':
        city = 'Melk'

    if not venue or not city:
        return None, None, None
    country_code = 'DE' if city in GERMAN_CITIES else 'AT'
    return venue, city, country_code


def work_text(work):
    if work.get('is_break'):
        return ''
    composer = clean_text(work.get('name'))
    title = clean_text(work.get('description'))
    if composer and title:
        return f'{composer}: {title}'
    return composer or title


def detail_description(detail, fallback=None):
    parts = []
    for key in ('description_long', 'description_short2', 'description_short'):
        value = clean_text(detail.get(key))
        if value and value not in parts:
            parts.append(value)

    works = [work_text(work) for work in detail.get('works') or []]
    works = [work for work in works if work]
    if works:
        parts.append('Programme\n' + '\n'.join(works))

    return '\n\n'.join(parts) or clean_text(fallback) or None


def make_record(event, detail=None):
    detail = detail or event
    title = clean_text(detail.get('name') or event.get('name'))
    start = detail.get('date_start') or event.get('date_start') or ''
    url = event_url(detail) or event_url(event)
    venue, city, country_code = resolve_location(detail)

    try:
        start_at = datetime.fromisoformat(start)
        event_date = date.fromisoformat(start_at.date().isoformat()).isoformat()
        time_from = start_at.strftime('%H:%M')
    except (TypeError, ValueError):
        return None

    if not title or not url or not venue or not city or not country_code:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': detail_description(detail, event.get('description_short')),
    }


def scrape_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = listing_events(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(get_json, session, f'{EVENTS_API}{event["id"]}/'): event
            for event in events
            if event.get('id')
        }
        for future in as_completed(futures):
            event = futures[future]
            try:
                record = make_record(event, future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape Tonkünstler concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=event_url(event),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                record = make_record(event)
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['venue']
        ),
    )


class TonkuenstlerAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='tonkuenstler_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        return scrape_concerts()


def main():
    TonkuenstlerAtCrawler().run()


if __name__ == '__main__':
    main()
