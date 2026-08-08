import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html import unescape
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.salzburgerfestspiele.at/'
SOURCE = 'Salzburger Festspiele'
SEASONS_URL = f'{SOURCE_URL}vue/filter/de/seasons'
CALENDAR_URL = f'{SOURCE_URL}vue/calendar/de/events'
ARRANGEMENT_URL = f'{SOURCE_URL}vue/components/de/arrangements'
CITY = 'Salzburg'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

# These are broadcasts rather than performances at a physical venue. Their
# API "location" values are station names such as OE1, ARTE, or 3Sat.
NON_PHYSICAL_TYPES = {'RADIO', 'TV', 'STREAMING'}


def clean_text(value):
    if not value:
        return ''
    text = unescape(str(value))
    if '<' in text:
        text = BeautifulSoup(text, 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url, **kwargs):
    response = session.get(url, timeout=45, **kwargs)
    response.raise_for_status()
    return response.json()


def post_json(session, url, payload):
    response = session.post(url, json=payload, timeout=90)
    response.raise_for_status()
    return response.json()


def active_seasons(session):
    seasons = get_json(
        session,
        SEASONS_URL,
        params={'season': 0, 'isFestSpielBezirk': 'false'},
    )
    return [season for season in seasons if season.get('key')]


def listing_events(session):
    events = []
    for season in active_seasons(session):
        start = str(season.get('start_date') or '')[:10]
        end = str(season.get('end_date') or '')[:10]
        try:
            date.fromisoformat(start)
            date.fromisoformat(end)
        except ValueError:
            continue
        events.extend(post_json(session, CALENDAR_URL, {
            'season': season['key'],
            'dateRange': [start, end],
        }))

    # The "all" and special-purpose seasons overlap, so calendar event ID is
    # the authoritative identity of a performance.
    return list({event['id']: event for event in events if event.get('id')}.values())


def local_start(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo('UTC'))
    return parsed.astimezone(ZoneInfo('Europe/Vienna'))


def work_lines(works):
    lines = []
    for work in works or []:
        author = clean_text(work.get('author'))
        title = clean_text(work.get('title'))
        note = clean_text(work.get('work_note'))
        line = ': '.join(part for part in (author, title) if part)
        if note:
            line = f'{line} — {note}' if line else note
        if line:
            lines.append(line)
        lines.extend(work_lines(work.get('works')))
    return lines


def description_from(detail, event):
    parts = []
    for key in (
        'preliminary_program', 'work_note', 'hint', 'additional_info',
        'language_note', 'additional_credits', 'coproduction_info',
    ):
        value = clean_text(detail.get(key) or event.get(key))
        if value and value not in parts:
            parts.append(value)
    works = work_lines(detail.get('works'))
    if works:
        parts.append('Programm\n' + '\n'.join(works))
    return '\n\n'.join(parts) or None


def make_record(event, detail=None):
    detail = detail or {}
    if clean_text(event.get('type')).upper() in NON_PHYSICAL_TYPES:
        return None

    title = clean_text(event.get('title') or detail.get('title'))
    header = clean_text(event.get('header') or detail.get('header'))
    if header and header.lower() not in title.lower():
        title = f'{header} — {title}'
    venue = clean_text(event.get('location') or detail.get('location'))
    start = local_start(event.get('start'))
    url = clean_text(event.get('link') or detail.get('link')) or SOURCE_URL
    if not title or not venue or not start or not url:
        return None

    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': url,
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': CITY,
        'country_code': 'AT',
        'description': description_from(detail, event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = listing_events(session)
    arrangement_ids = {event.get('arrangement_id') for event in events}
    arrangement_ids.discard(None)
    details = {}

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(
                get_json, session, f'{ARRANGEMENT_URL}/{arrangement_id}'
            ): arrangement_id
            for arrangement_id in arrangement_ids
        }
        for future in as_completed(futures):
            arrangement_id = futures[future]
            try:
                details[arrangement_id] = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape Salzburger Festspiele production detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=f'{ARRANGEMENT_URL}/{arrangement_id}',
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    records = [
        make_record(event, details.get(event.get('arrangement_id')))
        for event in events
    ]
    records = [record for record in records if record]
    return sorted(records, key=lambda record: (
        record['date'], record['time_from'] or '', record['title'], record['venue']
    ))


class SalzburgerFestspieleAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='salzburgerfestspiele_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    SalzburgerFestspieleAtCrawler().run()


if __name__ == '__main__':
    main()
