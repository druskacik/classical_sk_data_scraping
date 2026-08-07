import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.alexandrapalace.com/'
SOURCE = 'Alexandra Palace'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on/')
CITY = 'London'
DEFAULT_VENUE = 'Alexandra Palace'
FIRST_ARCHIVE_YEAR = 2011

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def archive_event_urls(session):
    """Return event URLs from every year retained by the broad calendar."""
    events = {}
    current_year = date.today().year
    for year in range(FIRST_ARCHIVE_YEAR, current_year + 2):
        soup = get_soup(session, LISTING_URL, params={'y': f'{year}-1'})
        # Older archive cards predate the site's taxonomy attributes, so the
        # complete mixed catalogue is sent to the potential-event classifier.
        for card in soup.select('.event_card_wrapper'):
            classes = set(card.get('class') or [])
            if 'cancelled' in classes:
                continue
            link = card.select_one('a.event_target[href]')
            if not link:
                continue
            url = urljoin(SOURCE_URL, link.get('href'))
            parsed = urlparse(url)
            if parsed.netloc.endswith('alexandrapalace.com') and parsed.path.startswith('/whats-on/'):
                dates = parse_listing_dates(card.select_one('.dates'))
                events.setdefault(url, set()).update(dates)
    return events


def event_schema(soup):
    for element in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(element.string or '')
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get('@type') == 'Event':
                return candidate
    return {}


def parse_date(value):
    text = clean_text(value)
    for fmt in ('%d %b %Y', '%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_listing_dates(value):
    text = clean_text(value).replace('–', '-').replace('—', '-')
    matches = re.findall(r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})', text)
    if not matches:
        return []
    try:
        end = datetime.strptime(' '.join(matches[-1]), '%d %b %Y').date()
        start = datetime.strptime(' '.join(matches[0]), '%d %b %Y').date()
        short_start = re.match(r'\s*(\d{1,2})\s*-', text)
        if short_start and len(matches) == 1:
            start = date(end.year, end.month, int(short_start.group(1)))
        cross_month_start = re.match(r'\s*(\d{1,2})\s+([A-Za-z]{3})\s*-', text)
        if cross_month_start and len(matches) == 1:
            start = datetime.strptime(
                f'{cross_month_start.group(1)} {cross_month_start.group(2)} {end.year}',
                '%d %b %Y',
            ).date()
    except ValueError:
        return []
    if end < start:
        return []
    # Short runs are normally separately ticketed performances. Very long
    # ranges are seasons/exhibitions and are represented by their start date.
    days = (end - start).days
    if days > 14:
        return [start.isoformat()]
    return [date.fromordinal(start.toordinal() + offset).isoformat() for offset in range(days + 1)]


def parse_time(value):
    match = re.search(r'\b([01]?\d|2[0-3])[:.]([0-5]\d)\b', clean_text(value))
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def timetable_occurrences(soup):
    records = []
    table = soup.select_one('#doors-opening-times')
    if not table:
        return records

    headers = [clean_text(cell).casefold() for cell in table.select('thead th')]
    start_index = next(
        (index for index, value in enumerate(headers) if 'start' in value), None
    )
    for row in table.select('tbody tr'):
        cells = row.find_all(['th', 'td'], recursive=False)
        if not cells:
            continue
        event_date = parse_date(cells[0])
        if not event_date:
            continue
        time_from = (
            parse_time(cells[start_index])
            if start_index is not None and start_index < len(cells)
            else None
        )
        records.append((event_date, time_from))
    return records


def schema_location(schema):
    location = schema.get('location') if isinstance(schema, dict) else {}
    if not isinstance(location, dict):
        return '', ''
    venue = clean_text(location.get('name'))
    address = location.get('address') or {}
    city = clean_text(address.get('addressLocality')) if isinstance(address, dict) else ''
    return venue, city


def detail_records(session, url, fallback_dates=None):
    soup = get_soup(session, url)
    schema = event_schema(soup)
    title = clean_text(soup.select_one('#event_content h1')) or clean_text(schema.get('name'))

    sidebar_parts = soup.select('.event_sidebar .event_details p')
    sidebar_venue = clean_text(sidebar_parts[-1]) if sidebar_parts else ''
    schema_venue, schema_city = schema_location(schema)
    venue = sidebar_venue or schema_venue or DEFAULT_VENUE
    city = schema_city or CITY

    occurrences = timetable_occurrences(soup)
    if not occurrences:
        start = clean_text(schema.get('startDate'))
        event_date = parse_date(start[:19])
        if event_date:
            occurrences = [(event_date, parse_time(start))]
    if not occurrences:
        occurrences = [(event_date, None) for event_date in (fallback_dates or [])]

    description_parts = []
    for content in soup.select('#event_content > .grid--item--1'):
        content = BeautifulSoup(str(content), 'html.parser')
        for element in content.select('h1, script, style, table, .event_buttons'):
            element.decompose()
        text = clean_text(content)
        if text:
            description_parts.append(text)
    description = '\n\n'.join(description_parts) or None

    if not title or not venue or not city:
        return []
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date, time_from in sorted(set(occurrences))
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = archive_event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(detail_records, session, url, dates): url
            for url, dates in events.items()
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Alexandra Palace event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda item: (item['date'], item['time_from'] or '', item['title'], item['url']),
    )


class AlexandraPalaceComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='alexandrapalace_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
    AlexandraPalaceComCrawler().run()


if __name__ == '__main__':
    main()
