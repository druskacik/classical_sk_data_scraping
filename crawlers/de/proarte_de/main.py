import re
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.proarte.de/de'
API_URL = f'{SOURCE_URL}/api/productions/'
CALENDAR_URL = f'{API_URL}calendar/'
SOURCE = 'ProArte Hamburg'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'de-DE,de;q=0.9',
}

HAMBURG_VENUES = {
    'Elbphilharmonie': 'Hamburg',
    'Laeiszhalle': 'Hamburg',
    'St. Michaelis': 'Hamburg',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def earliest_published_date(session):
    dates = get_json(session, CALENDAR_URL)
    parsed = []
    for value in dates:
        try:
            parsed.append(datetime.strptime(value, '%d.%m.%Y').date())
        except (TypeError, ValueError):
            continue
    return min(parsed).strftime('%d.%m.%Y') if parsed else None


def listing_events(session):
    # Supplying the first date from the public calendar makes the API include
    # its still-published archive as well as all forthcoming concerts.
    first_date = earliest_published_date(session)
    url = API_URL
    params = {'date': first_date} if first_date else None
    events = []
    while url:
        payload = get_json(session, url, params=params)
        events.extend(payload.get('results') or [])
        url = payload.get('next')
        params = None
    return events


def resolve_location(event):
    display_name = clean_text((event.get('room') or {}).get('display_name'))
    if not display_name:
        return None, None

    if display_name.startswith('Kiel,'):
        return display_name.split(',', 1)[1].strip(), 'Kiel'
    if display_name.startswith('Philharmonie Berlin'):
        return display_name, 'Berlin'
    for venue_name, city in HAMBURG_VENUES.items():
        if display_name.startswith(venue_name):
            return display_name, city
    return None, None


def description(event):
    parts = []
    narrative = clean_text(event.get('program_text'))
    programme = clean_text(event.get('program'))
    if narrative:
        parts.append(narrative)
    if programme:
        parts.append('Programm\n' + programme)
    return '\n\n'.join(parts) or None


def make_record(event):
    title = clean_text(event.get('title'))
    subtitle = clean_text(event.get('subtitle'))
    if subtitle and subtitle.lower() not in title.lower():
        title = f'{title} – {subtitle}'

    raw_date = event.get('date') or ''
    try:
        start = datetime.fromisoformat(raw_date)
        event_date = date(start.year, start.month, start.day).isoformat()
    except (TypeError, ValueError):
        return None

    url = event.get('get_absolute_url') or ''
    venue, city = resolve_location(event)
    if not title or not url or not venue or not city:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'DE',
        'description': description(event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        events = listing_events(session)
    except (requests.RequestException, ValueError) as error:
        log_message(
            'Failed to fetch ProArte concert catalogue',
            event='crawler_fetch_failed',
            level='error',
            url=API_URL,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        raise

    records = [record for event in events if (record := make_record(event))]
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class ProarteDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='proarte_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
        upload_target='classical',
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
    ProarteDeCrawler().run()


if __name__ == '__main__':
    main()
