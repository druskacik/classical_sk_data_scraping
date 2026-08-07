import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://santacecilia.it/'
CALENDAR_URL = urljoin(SOURCE_URL, 'concerti/calendario-concerti/')
SOURCE = 'Accademia Nazionale di Santa Cecilia'
ROME_VENUES = {
    'sala santa cecilia',
    'sala sinopoli',
    'sala petrassi',
    'teatro studio',
    "teatro dell'opera di roma",
    'auditorium parco della musica',
}
FOREIGN_CITIES = {
    'antwerp': 'BE',
    'anversa': 'BE',
    'barcellona': 'ES',
    'bratislava': 'SK',
    'budapest': 'HU',
    'dortmund': 'DE',
    'lussemburgo': 'LU',
    'madrid': 'ES',
    'parigi': 'FR',
    'vienna': 'AT',
    'zagabria': 'HR',
}
MONTHS = {
    'gen': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'mag': 5, 'giu': 6,
    'lug': 7, 'ago': 8, 'set': 9, 'ott': 10, 'nov': 11, 'dic': 12,
}
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'it-IT,it;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        value = value.get_text('\n', strip=True)
    value = str(value).replace('\xa0', ' ').replace('\u200b', '')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def get_html(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.text


def listing_items(session):
    items = {}
    page = 1
    while True:
        params = {'more': 1, 'pg': page} if page > 1 else None
        soup = BeautifulSoup(get_html(session, CALENDAR_URL, params), 'html.parser')
        before = len(items)
        for article in soup.select('#archiveReplace article.replicaLoop'):
            link = article.select_one('.titolo a[href*="/concerto/"]')
            replica_id = (article.get('id') or '').replace('boxReplica', '')
            if not link or not replica_id:
                continue
            items[replica_id] = {
                'title': clean_text(link),
                'url': urljoin(SOURCE_URL, link.get('href')),
                'month': MONTHS.get(clean_text(article.select_one('.dateM')).lower()[:3]),
                'day': clean_text(article.select_one('.giorno')),
                'location': clean_text(article.select_one('.luogo')),
                'time': clean_text(article.select_one('.orario')),
            }
        if not soup.select_one('.loadMore') or len(items) == before:
            break
        page += 1
    return list(items.values())


def parse_date(detail, item):
    candidates = []
    for cell in detail.select('.qthemeCalendar td.active[date]'):
        raw = cell.get('date', '')
        if re.fullmatch(r'\d{8}', raw):
            try:
                candidates.append(date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat())
            except ValueError:
                pass
    for candidate in candidates:
        parsed = date.fromisoformat(candidate)
        if parsed.month == item['month'] and str(parsed.day) == item['day'].lstrip('0'):
            return candidate
    return None


def detail_location(description, city):
    if not description or not city:
        return None
    for line in description.splitlines():
        match = re.match(rf'{re.escape(city)}\s*,\s*(.+?)(?:\s+[–-]\s+.*)?$', line, re.I)
        if match:
            venue = clean_text(match.group(1)).rstrip(' .–-')
            if venue and venue.lower() != city.lower():
                return venue
    return None


def resolve_location(item, description):
    location = clean_text(item['location']).strip(' ,')
    if not location:
        return None, None, None
    if location.lower() in ROME_VENUES:
        return location, 'Roma', 'IT'

    parts = [part.strip() for part in location.split(',', 1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        city, venue = parts
    else:
        city = location
        venue = detail_location(description, city)
    if not venue:
        return None, None, None
    country_code = FOREIGN_CITIES.get(city.lower(), 'IT')
    return venue, city, country_code


def parse_item(session, item):
    detail = BeautifulSoup(get_html(session, item['url']), 'html.parser')
    event_date = parse_date(detail, item)
    description = clean_text(detail.select_one('.specificheProgramma')) or None
    venue, city, country_code = resolve_location(item, description)
    time_match = re.search(r'(\d{1,2}):(\d{2})', item['time'])
    if not item['title'] or not event_date or not venue or not city or not country_code:
        return None
    return {
        'title': item['title'],
        'date': event_date,
        'url': item['url'],
        'time_from': f'{int(time_match.group(1)):02d}:{time_match.group(2)}' if time_match else None,
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
    items = listing_items(session)
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(parse_item, session, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                record = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Santa Cecilia concert detail',
                    event='crawler_item_failed', level='warning', url=item['url'],
                    error_type=type(error).__name__, error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(records, key=lambda row: (row['date'], row['time_from'] or '', row['title']))


class SantaCeciliaItCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='santacecilia_it',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='IT',
        # The academy calendar also hosts jazz, world-music, and festival events.
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
    SantaCeciliaItCrawler().run()


if __name__ == '__main__':
    main()
