import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.kravis.org/'
SOURCE = 'Kravis Center for the Performing Arts'
EVENTS_API = f'{SOURCE_URL}wp-json/wp/v2/events'
CALENDAR_API = f'{SOURCE_URL}wp-json/kravis/v1/performances-by-month'
CITY = 'West Palm Beach'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = html.unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'\[(?:/?vc_[^\]]+|/?[a-z_]+[^\]]*)\]', ' ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def new_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get_catalogue():
    session = new_session()
    # Establishing a normal first-party session avoids the site's intermittent
    # bot-check response on direct REST requests.
    session.get(SOURCE_URL, timeout=45).raise_for_status()
    page = 1
    events = []
    while True:
        response = session.get(
            EVENTS_API,
            params={'per_page': 100, 'page': page, 'status': 'publish'},
            timeout=45,
        )
        response.raise_for_status()
        batch = response.json()
        events.extend(batch)
        total_pages = int(response.headers.get('X-WP-TotalPages', '1'))
        if page >= total_pages:
            return events
        page += 1


def parse_showtime(value):
    value = clean_text(value).replace(' at ', ' @ ')
    match = re.search(
        r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+'
        r'([A-Z][a-z]{2}\s+\d{1,2}\s+\d{4})\s+@\s+'
        r'(\d{1,2}:\d{2}\s*[ap]m)',
        value,
        flags=re.I,
    )
    if not match:
        return None
    try:
        date_value = datetime.strptime(match.group(1), '%b %d %Y').date().isoformat()
        time_value = datetime.strptime(
            re.sub(r'\s+', '', match.group(2)).upper(), '%I:%M%p'
        ).strftime('%H:%M')
    except ValueError:
        return None
    return date_value, time_value


def month_starts(first_year, last_year):
    return [date(year, month, 1) for year in range(first_year, last_year + 1) for month in range(1, 13)]


def get_calendar_month(month):
    response = new_session().get(
        CALENDAR_API,
        params={'date': month.strftime('%a, %d %b %Y 00:00:00 GMT')},
        timeout=45,
    )
    response.raise_for_status()
    return (response.json().get('data') or {}).get('performances') or []


def parse_event(event, catalogue_event):
    url = event.get('link') or catalogue_event.get('link') or ''
    title = clean_text(event.get('title') or (catalogue_event.get('title') or {}).get('rendered'))
    title = re.sub(r'\s*\n\s*', ' – ', title)
    venue = clean_text(event.get('location'))
    if not url or not title or not venue:
        return []

    description = clean_text((catalogue_event.get('content') or {}).get('rendered')) or None
    records = []
    seen = set()
    for occurrence in event.get('dates') or []:
        parsed = parse_showtime(occurrence.get('date'))
        if not parsed or parsed in seen:
            continue
        seen.add(parsed)
        records.append({
            'title': title,
            'date': parsed[0],
            'url': url,
            # The feed uses midnight for all-day installations rather than an
            # advertised start time.
            'time_from': None if parsed[1] == '00:00' else parsed[1],
            'venue': venue,
            'city': CITY,
            'country_code': 'US',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    catalogue = get_catalogue()
    catalogue_by_id = {event['id']: event for event in catalogue}
    first_year = min(datetime.fromisoformat(event['date']).year for event in catalogue)
    # Published event posts reveal how far back the site's live archive goes.
    # Three future years comfortably covers seasons announced well in advance.
    months = month_starts(first_year, date.today().year + 3)
    events_by_id = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_calendar_month, month): month for month in months}
        for future in as_completed(futures):
            month = futures[future]
            try:
                for event in future.result():
                    if event.get('id') in catalogue_by_id:
                        events_by_id[event['id']] = event
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape calendar month',
                    event='crawler_page_failed',
                    level='warning',
                    url=CALENDAR_API,
                    month=month.isoformat(),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    records = []
    for event_id, event in events_by_id.items():
        records.extend(parse_event(event, catalogue_by_id[event_id]))

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class KravisOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='kravis_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
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
    KravisOrgCrawler().run()


if __name__ == '__main__':
    main()
