import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.osr.ch/en/'
CONCERTS_URL = urljoin(SOURCE_URL, 'concerts-tickets/concerts')
ARCHIVES_URL = urljoin(SOURCE_URL, 'concerts-tickets/archives')
ARCHIVE_SEARCH_URL = urljoin(ARCHIVES_URL + '/', 'search')
SOURCE = 'Orchestre de la Suisse Romande'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# The OSR calendar includes tours.  Only locations which can be identified
# confidently are retained; unknown locations are never silently labelled CH.
CITY_COUNTRIES = {
    'amsterdam': 'NL',
    'athens': 'GR',
    'basel': 'CH',
    'berlin': 'DE',
    'bern': 'CH',
    'brussels': 'BE',
    'bucharest': 'RO',
    'budapest': 'HU',
    'copenhagen': 'DK',
    'frankfurt': 'DE',
    'geneva': 'CH',
    'genève': 'CH',
    'gstaad': 'CH',
    'hamburg': 'DE',
    'lausanne': 'CH',
    'london': 'GB',
    'lucerne': 'CH',
    'lugano': 'CH',
    'lyon': 'FR',
    'madrid': 'ES',
    'milan': 'IT',
    'montreux': 'CH',
    'moscow': 'RU',
    'munich': 'DE',
    'new york': 'US',
    'paris': 'FR',
    'prague': 'CZ',
    'rome': 'IT',
    'salzburg': 'AT',
    'san francisco': 'US',
    'seoul': 'KR',
    'shanghai': 'CN',
    'tokyo': 'JP',
    'vevey': 'CH',
    'vienna': 'AT',
    'warsaw': 'PL',
    'zurich': 'CH',
    'zürich': 'CH',
}

VENUE_CITIES = {
    'bâtiment des forces motrices': 'Geneva',
    'casino du rivage': 'Vevey',
    'cathédrale saint-pierre': 'Geneva',
    'conservatoire de musique de genève': 'Geneva',
    'grand théâtre de genève': 'Geneva',
    'la cité bleue': 'Geneva',
    'salle communale de plainpalais': 'Geneva',
    'temple de saint-françois': 'Lausanne',
    'victoria hall': 'Geneva',
}


def clean_text(value):
    if value is None:
        return ''
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value):
    try:
        return datetime.strptime(clean_text(value), '%d.%m.%Y').date().isoformat()
    except ValueError:
        return None


def parse_location(value):
    """Parse strings such as ``21:15 — Victoria Hall, Geneva``."""
    text = clean_text(value)
    match = re.match(r'^(\d{1,2})\s*[h:]\s*(\d{2})\s*[—–-]\s*(.+)$', text)
    if not match:
        return None
    location = match.group(3).strip()
    if ',' not in location:
        return None
    venue, city = (part.strip() for part in location.rsplit(',', 1))
    country_code = CITY_COUNTRIES.get(city.casefold())
    if not venue or not city or not country_code:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}', venue, city, country_code


def section_text(soup, heading):
    node = soup.find(['h2', 'h3'], string=lambda value: value and clean_text(value) == heading)
    if not node:
        return ''
    container = node.parent
    return clean_text(container.get_text('\n', strip=True))


def parse_detail(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title_node = soup.select_one('h1')
    date_node = soup.select_one('p.date')
    if not title_node or not date_node:
        return None

    location_node = date_node.find_next_sibling('p')
    location = parse_location(location_node.get_text(' ', strip=True) if location_node else '')
    date = parse_date(date_node.get_text(' ', strip=True))
    title = clean_text(title_node.get_text(' ', strip=True))
    if not title or not date or not location:
        return None

    time_from, venue, city, country_code = location
    description_parts = [
        section_text(soup, 'Programme'),
        section_text(soup, 'The music'),
    ]
    description = clean_text('\n\n'.join(filter(None, description_parts))) or None
    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def parse_archive_item(item):
    title_node = item.select_one('h4')
    date_node = item.select_one('.concert .date')
    location_node = date_node.find_next_sibling('p') if date_node else None
    if not date_node or not location_node:
        return None

    location_text = clean_text(location_node.get_text('\n', strip=True))
    lines = location_text.splitlines()
    match = re.match(r'^(\d{1,2})\s*[h:]\s*(\d{2})\s*[—–-]\s*(.+)$', lines[0])
    if not match:
        return None
    venue = clean_text(match.group(3))
    city = clean_text(lines[-1]) if len(lines) >= 2 else VENUE_CITIES.get(venue.casefold(), '')
    country_code = CITY_COUNTRIES.get(city.casefold())
    date = parse_date(date_node.get_text(' ', strip=True))
    title = clean_text(title_node.get_text(' ', strip=True)) if title_node else ''
    if not title and date:
        title = f'OSR concert — {date}'
    if not title or not date or not country_code:
        return None
    if not venue:
        return None

    wrapper = item.select_one('[class*="uid"]')
    uid_class = next((c for c in (wrapper.get('class') if wrapper else []) if c.startswith('uid')), '')
    url = f'{ARCHIVES_URL}#{uid_class}' if uid_class else ARCHIVES_URL
    concert = item.select_one('.concert')
    description = clean_text(concert.get_text('\n', strip=True)) if concert else ''
    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': f'{int(match.group(1)):02d}:{match.group(2)}',
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def fetch(url, params=None):
    response = requests.get(url, params=params, headers=HEADERS, timeout=45)
    response.raise_for_status()
    return response.text


def current_concerts():
    soup = BeautifulSoup(fetch(CONCERTS_URL), 'html.parser')
    urls = sorted({
        urljoin(SOURCE_URL, link['href'])
        for link in soup.select('a[href*="/event/"][href]')
    })
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_detail(future.result(), url)
                if record:
                    records.append(record)
                else:
                    log_message('Skipped incomplete concert', event='crawler_item_skipped', level='warning', url=url)
            except requests.RequestException as error:
                log_message(
                    'Concert detail request failed', event='crawler_request_failed', level='warning',
                    url=url, error_type=type(error).__name__, error_message=str(error),
                )
    return records


def archived_concerts():
    # Searching once per year is the archive's only enumerable interface.  The
    # results themselves contain complete concert and programme information.
    years = range(1918, datetime.now().year + 1)
    records = []
    params_for = lambda year: {
        'tx_osrpackage_archive[action]': 'search',
        'tx_osrpackage_archive[controller]': 'Archive',
        'tx_osrpackage_archive[searchArg]': str(year),
    }
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch, ARCHIVE_SEARCH_URL, params_for(year)): year for year in years}
        for future in as_completed(futures):
            year = futures[future]
            try:
                soup = BeautifulSoup(future.result(), 'html.parser')
                for item in soup.select('.archive-item'):
                    record = parse_archive_item(item)
                    if record:
                        records.append(record)
            except requests.RequestException as error:
                log_message(
                    'Archive year request failed', event='crawler_request_failed', level='warning',
                    url=ARCHIVE_SEARCH_URL, year=year, error_type=type(error).__name__,
                    error_message=str(error),
                )
    return records


class OsrChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='osr_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        records = current_concerts() + archived_concerts()
        return sorted(records, key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ))


def main():
    OsrChCrawler().run()


if __name__ == '__main__':
    main()
