import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.opera.hu/hu/'
CALENDAR_URL = urljoin(SOURCE_URL, 'ajax/calendar/line/')
SOURCE = 'Magyar Állami Operaház'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'hu-HU,hu;q=0.9,en;q=0.7',
}

HOME_VENUE_MARKERS = (
    'Magyar Állami Operaház',
    'Operaház',
    'Eiffel Műhelyház',
    'Erkel Színház',
    'OperaSafe Emlékgyűjtemény',
)
EXTERNAL_VENUES = {
    'Bukaresti Nemzeti Opera': ('Bukarest', 'RO'),
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    # Some calendar fragments contain entities encoded twice.
    text = html.unescape(html.unescape(text))
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response


def performance_datetime(url):
    match = re.search(r'-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})/?$', url)
    if not match:
        match = re.search(r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})/?$', url)
    if not match:
        return None
    try:
        event_date = date(*map(int, match.group(1, 2, 3))).isoformat()
        hour, minute = map(int, match.group(4, 5))
    except ValueError:
        return None
    if hour > 23 or minute > 59:
        return None
    return event_date, f'{hour:02d}:{minute:02d}'


def resolve_location(location):
    venue = clean_text(location)
    if not venue:
        return None
    if venue in EXTERNAL_VENUES:
        city, country_code = EXTERNAL_VENUES[venue]
        return venue, city, country_code
    if any(marker.casefold() in venue.casefold() for marker in HOME_VENUE_MARKERS):
        return venue, 'Budapest', 'HU'
    # Tour pages often expose only a town as "Helyszín". A town cannot also
    # serve as the venue, so those entries are deliberately skipped.
    return None


def calendar_items(session):
    items = {}
    # The public endpoint still exposes a small archive beginning in 2014.
    # Query one calendar year at a time to keep responses bounded, through the
    # end of next year (the furthest season normally published by the site).
    for year in range(2014, date.today().year + 2):
        days = (date(year + 1, 1, 1) - date(year, 1, 1)).days
        try:
            response = get_response(
                session,
                CALENDAR_URL,
                params={'lan': 'hu', 'from': f'{year}-01-01', 'days': days},
            )
        except requests.RequestException as error:
            log_message(
                'Failed to scrape OPERA calendar year',
                event='crawler_page_failed',
                level='warning',
                url=CALENDAR_URL,
                year=year,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue

        soup = BeautifulSoup(response.content, 'html.parser')
        for anchor in soup.select('a.calendar-modal-event[href]'):
            url = urljoin(SOURCE_URL, anchor.get('href'))
            parsed = performance_datetime(url)
            title = clean_text(anchor.select_one('.calendar-modal-event-title'))
            if not parsed or not title:
                continue
            items[url] = {
                'url': url,
                'title': title,
                'date': parsed[0],
                'time_from': parsed[1],
                'author': clean_text(anchor.select_one('.calendar-modal-event-author')),
                'location': clean_text(anchor.select_one('.calendar-modal-event-location')),
            }
    return list(items.values())


def detail_data(session, item):
    soup = BeautifulSoup(get_response(session, item['url']).content, 'html.parser')
    title = clean_text(soup.find('h1')) or item['title']
    location = clean_text(soup.select_one('.pd-location')) or item['location']

    parts = []
    if item['author']:
        parts.append(item['author'])
    for selector in ('.project--short', '.project-description'):
        for section in soup.select(selector):
            text = clean_text(section)
            if text and text not in parts:
                parts.append(text)
    return title, location, '\n\n'.join(parts) or None


def make_record(item, detail=None):
    title, location, description = detail or (
        item['title'], item['location'], item['author'] or None
    )
    resolved = resolve_location(location)
    if not title or not resolved:
        return None
    venue, city, country_code = resolved
    return {
        'title': title,
        'date': item['date'],
        'url': item['url'],
        'time_from': item['time_from'],
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = calendar_items(session)
    records = []

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(detail_data, session, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                record = make_record(item, future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape OPERA performance detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                record = make_record(item)
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda row: (row['date'], row['time_from'] or '', row['title'], row['url']),
    )


class OperaHuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='opera_hu',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='HU',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    OperaHuCrawler().run()


if __name__ == '__main__':
    main()
