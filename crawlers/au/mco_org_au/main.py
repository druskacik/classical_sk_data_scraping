import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://mco.org.au/'
EVENTS_API = f'{SOURCE_URL}wp-json/wp/v2/events'
SOURCE = 'Melbourne Chamber Orchestra'

HEADERS = {
    # The site allows its public, search-indexable pages to crawler user agents,
    # while its normal browser route is protected by a Cloudflare challenge.
    'User-Agent': 'Googlebot',
    'Accept': 'application/json,text/html;q=0.9,*/*;q=0.8',
}

MONTHS = {
    name: number
    for number, names in enumerate(
        (
            (),
            ('jan', 'january'),
            ('feb', 'february'),
            ('mar', 'march'),
            ('apr', 'april'),
            ('may',),
            ('jun', 'june'),
            ('jul', 'july'),
            ('aug', 'august'),
            ('sep', 'sept', 'september'),
            ('oct', 'october'),
            ('nov', 'november'),
            ('dec', 'december'),
        )
    )
    for name in names
}

TEXT_DATE_RE = re.compile(
    r'(?i)\b(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)?\s*'
    r'(\d{1,2})\s+([a-z]{3,9})\s+(\d{2,4})\b'
)
NUMERIC_DATE_RE = re.compile(r'\b(\d{1,2})-(\d{1,2})-(\d{4})\b')
TIME_RE = re.compile(r'(?i)\b(\d{1,2})[:.](\d{2})\s*(am|pm)\b')


def clean_text(value, separator=' '):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text(separator, strip=True)
    text = html.unescape(text).replace('\xa0', ' ').replace('\u202f', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def parse_date(value):
    match = NUMERIC_DATE_RE.search(value)
    if match:
        day, month, year = map(int, match.groups())
    else:
        match = TEXT_DATE_RE.search(value)
        if not match:
            return None
        day = int(match.group(1))
        month = MONTHS.get(match.group(2).lower())
        year = int(match.group(3))
        if month is None:
            return None
        if year < 100:
            year += 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = TIME_RE.search(value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour not in range(1, 13) or minute not in range(60):
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def normalise_city(value):
    city = clean_text(value).strip(' ,')
    city = re.sub(r',\s*SOUTH AUSTRALIA$', '', city, flags=re.I)
    if city.upper() == 'MT BARKER':
        city = 'Mount Barker'
    if not city or city.upper() in {
        'ONLINE',
        'FULL PROGRAM',
        'OPTIONAL COACH SERVICE',
        'INDIVIDUAL CONCERT TICKETS',
    }:
        return None
    return city.title()


def normalise_venue(value):
    venue = clean_text(value).strip(' —-')
    venue = re.split(
        r'(?i)\s+(?:BOOK(?:INGS)?|SOLD OUT|PRESENTED AS|\* PRESENTED AS)\b',
        venue,
        maxsplit=1,
    )[0].strip(' —-')
    # The site uses its street address as the display name for MCO's own
    # performance space. Keep the record but emit a real venue name.
    if re.fullmatch(r'75 Reid Street(?:, Fitzroy North)?', venue, flags=re.I):
        return 'Melbourne Chamber Orchestra Office'
    # Addresses are not valid venue names when no institution name is given.
    if re.match(r'^\d+\s+', venue):
        return None
    return venue or None


def event_description(event, soup):
    parts = []
    body = clean_text((event.get('content') or {}).get('rendered'))
    if body:
        parts.append(body)
    music = soup.select_one('.the-music')
    music_text = clean_text(music, separator='\n')
    if music_text and music_text not in parts:
        parts.append(music_text)
    return '\n\n'.join(parts) or None


def parse_event(event, page_html):
    soup = BeautifulSoup(page_html, 'html.parser')
    title = clean_text((event.get('title') or {}).get('rendered'))
    url = event.get('link') or ''
    if not title or not url:
        return []

    description = event_description(event, soup)
    records = []
    for item in soup.select('.where-when .location-list > li'):
        segments = [clean_text(part) for part in item.stripped_strings]
        segments = [part for part in segments if part]
        date_index = next((i for i, part in enumerate(segments) if parse_date(part)), None)
        if date_index is None or date_index == 0 or date_index + 1 >= len(segments):
            continue
        # Festival/package entries publish a date span rather than an
        # individual concert. Their constituent concerts have their own
        # event pages, so do not manufacture a single date for the package.
        if re.search(r'\b\d{1,2}\s*-\s*\d{1,2}\s+[A-Za-z]', segments[date_index]):
            continue

        event_date = parse_date(segments[date_index])
        city = normalise_city(segments[date_index - 1])
        venue = normalise_venue(segments[date_index + 1])
        if not event_date or not city or not venue:
            continue

        records.append(
            {
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': parse_time(segments[date_index]),
                'venue': venue,
                'city': city,
                'country_code': 'AU',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )
    return records


def get_json(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = get_json(
        session,
        EVENTS_API,
        params={'per_page': 100, 'orderby': 'date', 'order': 'desc'},
    )
    records = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(session.get, event.get('link'), timeout=45): event
            for event in events
            if event.get('link')
        }
        for future in as_completed(futures):
            event = futures[future]
            try:
                response = future.result()
                response.raise_for_status()
                records.extend(parse_event(event, response.text))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=event.get('link'),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class McoOrgAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mco_org_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
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
    McoOrgAuCrawler().run()


if __name__ == '__main__':
    main()
