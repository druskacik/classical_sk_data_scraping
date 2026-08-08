import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://musikverein-graz.at/'
EVENTS_API = f'{SOURCE_URL}wp-json/wp/v2/konzerte'
SOURCE = 'Musikverein Graz'
CITY = 'Graz'
MONTHS = {
    'januar': 1,
    'februar': 2,
    'märz': 3,
    'april': 4,
    'mai': 5,
    'juni': 6,
    'juli': 7,
    'august': 8,
    'september': 9,
    'oktober': 10,
    'november': 11,
    'dezember': 12,
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    value = str(value)
    text = (
        BeautifulSoup(value, 'html.parser').get_text('\n', strip=True)
        if '<' in value
        else html.unescape(value)
    )
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url, params=None):
    response = session.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response.json(), response.headers


def listing_events(session):
    """Return every published concert post, including the site's archive."""
    events = []
    page = 1
    while True:
        batch, headers = get_json(
            session,
            EVENTS_API,
            params={
                'per_page': 100,
                'page': page,
                'orderby': 'date',
                'order': 'desc',
                '_fields': 'id,link,title',
            },
        )
        events.extend(batch)
        if page >= int(headers.get('X-WP-TotalPages', page)):
            return events
        page += 1


def event_date(event):
    title = clean_text((event.get('title') or {}).get('rendered'))
    match = re.match(r'^\[(\d{1,2})[.-](\d{1,2})[.-](\d{2,4})\]', title)
    if not match:
        return None
    try:
        year = int(match.group(3))
        if year < 100:
            year += 2000
        return date(year, int(match.group(2)), int(match.group(1))).isoformat()
    except ValueError:
        return None


def detail_date(details):
    month_year = clean_text(details.select_one('.datum .monat')).lower().split()
    day_text = clean_text(details.select_one('.datum .date'))
    if len(month_year) != 2 or not day_text.isdigit():
        return None
    month = MONTHS.get(month_year[0])
    if not month:
        return None
    try:
        year = int(month_year[1])
        if year < 100:
            year += 2000
        return date(year, month, int(day_text)).isoformat()
    except ValueError:
        return None


def field_after_label(details, selector):
    label = details.select_one(selector)
    if label is None or label.parent is None:
        return ''
    text = clean_text(label.parent)
    label_text = clean_text(label)
    return text.removeprefix(label_text).strip().strip(':').strip()


def parse_time(value):
    match = re.search(r'(?<!\d)([01]?\d|2[0-3])[.:]([0-5]\d)(?!\d)', value)
    if match:
        return f'{int(match.group(1)):02d}:{match.group(2)}'
    return None


def description_text(soup):
    programme = soup.select_one('.programm')
    if programme is None:
        return None
    for unwanted in programme.select(
        '.iconsNeu, .termine, .brlbs-cmpnt-container, script, style'
    ):
        unwanted.decompose()
    return clean_text(programme) or None


def make_record(event, html):
    soup = BeautifulSoup(html, 'html.parser')
    details = soup.select_one('.details.konzertDetail')
    title = re.sub(r'\s+', ' ', clean_text(soup.select_one('.titleCol h1'))).strip()
    url = clean_text(event.get('link'))
    if details is None:
        return None
    concert_date = detail_date(details) or event_date(event)

    venue = field_after_label(details, '.ort')
    time_text = field_after_label(details, '.uhrzeit')
    if not title or not concert_date or not url or not venue:
        return None

    return {
        'title': title,
        'date': concert_date,
        'url': url,
        'time_from': parse_time(time_text),
        'venue': venue,
        'city': CITY,
        'country_code': 'AT',
        'description': description_text(soup),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class MusikvereinGrazAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='musikverein_graz_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        # The calendar also contains house tours, competitions, and occasional
        # institutional events, so classification is required before upload.
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
        session = requests.Session()
        session.headers.update(HEADERS)
        events = listing_events(session)
        records = []

        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {
                executor.submit(session.get, event.get('link'), timeout=60): event
                for event in events
                if event.get('link')
            }
            for future in as_completed(futures):
                event = futures[future]
                url = clean_text(event.get('link'))
                try:
                    response = future.result()
                    response.raise_for_status()
                    record = make_record(event, response.text)
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape Musikverein Graz concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                    continue

                if record:
                    records.append(record)
                else:
                    log_message(
                        'Skipped incomplete Musikverein Graz concert',
                        event='crawler_item_skipped',
                        level='warning',
                        url=url,
                        error_type='IncompleteEventData',
                        error_message='Required date, title, URL, venue, or city is missing',
                    )

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    MusikvereinGrazAtCrawler().run()


if __name__ == '__main__':
    main()
