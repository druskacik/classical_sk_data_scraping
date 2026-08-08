import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.asc.at/'
UPCOMING_URL = urljoin(SOURCE_URL, 'kommende-projekte/')
ARCHIVE_URL = urljoin(SOURCE_URL, 'past-events/')
SOURCE = 'Arnold Schoenberg Chor'

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
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value):
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except (TypeError, ValueError):
        pass
    match = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', clean_text(value))
    if not match:
        return None
    try:
        return date(int(match.group(3)), int(match.group(1)), int(match.group(2))).isoformat()
    except ValueError:
        return None


def split_location(value):
    # The list consistently renders location as "city - venue". Splitting only
    # on a spaced hyphen preserves hyphens that are part of a venue name.
    parts = re.split(r'\s+-\s+', clean_text(value), maxsplit=1)
    if len(parts) != 2:
        return None, None
    city, venue = (part.strip() for part in parts)
    return (city or None), (venue or None)


def resolve_country(city):
    normalized = city.casefold()
    markers = {
        '(fin)': 'FI', '(finnland)': 'FI',
        '(hun)': 'HU', '(ungarn)': 'HU',
        '(deu)': 'DE', '(ger)': 'DE', '(deutschland)': 'DE',
        '(che)': 'CH', '(sui)': 'CH', '(schweiz)': 'CH',
        '(ita)': 'IT', '(italien)': 'IT',
    }
    for marker, country_code in markers.items():
        if marker in normalized:
            return re.sub(r'\s*\([^)]*\)\s*$', '', city).strip(), country_code
    if normalized in {'györ', 'győr'}:
        return city, 'HU'
    return city, 'AT'


def listing_records(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    records = []
    for item in soup.select('li.single_event_list'):
        link = item.select_one('a[href*="/js_events/"]')
        title = clean_text(item.select_one('.event_location'))
        event_date = parse_date(clean_text(item.select_one('.event_date')))
        city, venue = split_location(item.select_one('.event_venue'))
        event_url = urljoin(url, link.get('href', '').strip()) if link else ''
        if city:
            city, country_code = resolve_country(city)
        else:
            country_code = None
        if not title or not event_date or not event_url or not city or not venue:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': event_url,
            'time_from': None,
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': None,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def add_detail(session, record):
    response = session.get(record['url'], timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    start = soup.select_one('[itemprop="startDate"]')
    if start:
        detail_date = parse_date(start.get('content') or clean_text(start))
        if detail_date:
            record['date'] = detail_date

    details = soup.select_one('.event_short_details')
    if details:
        time_match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', clean_text(details))
        if time_match:
            record['time_from'] = f'{int(time_match.group(1)):02d}:{time_match.group(2)}'

    city_node = soup.select_one('[itemprop="address"]')
    venue_node = soup.select_one('.swp_location_schema [itemprop="name"]')
    detail_city = clean_text(city_node)
    detail_venue = clean_text(venue_node)
    if detail_city and detail_venue:
        record['city'], record['country_code'] = resolve_country(detail_city)
        record['venue'] = detail_venue

    description = clean_text(soup.select_one('.swp_event_content[itemprop="description"]'))
    record['description'] = description or None
    return record


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    for url in (UPCOMING_URL, ARCHIVE_URL):
        records.extend(listing_records(session, url))

    # Both pages can briefly contain the same event while the site moves it
    # into the archive.
    records = list({record['url']: record for record in records}.values())
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(add_detail, session, record): record for record in records}
        for future in as_completed(futures):
            record = futures[future]
            try:
                future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape ASC concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=record['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class AscAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='asc_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
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
    AscAtCrawler().run()


if __name__ == '__main__':
    main()
