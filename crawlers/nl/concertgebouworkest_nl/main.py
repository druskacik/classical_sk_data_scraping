import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.concertgebouworkest.nl/nl/'
AGENDA_URL = urljoin(SOURCE_URL, 'agenda/')
SOURCE = 'Koninklijk Concertgebouworkest'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'nl-NL,nl;q=0.9,en;q=0.7',
}
COUNTRIES = {
    'Nederland': 'NL', 'Duitsland': 'DE', 'België': 'BE',
    'Frankrijk': 'FR', 'Oostenrijk': 'AT', 'Italië': 'IT',
    'Spanje': 'ES', 'Portugal': 'PT', 'Verenigd Koninkrijk': 'GB',
    'Engeland': 'GB', 'Schotland': 'GB', 'Ierland': 'IE',
    'Zwitserland': 'CH', 'Luxemburg': 'LU', 'Denemarken': 'DK',
    'Zweden': 'SE', 'Noorwegen': 'NO', 'Finland': 'FI',
    'Tsjechië': 'CZ', 'Polen': 'PL', 'Hongarije': 'HU',
    'Roemenië': 'RO', 'Griekenland': 'GR', 'Estland': 'EE',
    'Letland': 'LV', 'Litouwen': 'LT', 'Verenigde Staten': 'US',
    'Canada': 'CA', 'Japan': 'JP', 'China': 'CN', 'Zuid-Korea': 'KR',
    'Zuid Korea': 'KR', 'Slovenië': 'SI', 'IJsland': 'IS',
}
CITY_COUNTRIES = {
    'Baden-Baden': 'DE', 'Salzburg': 'AT', 'London': 'GB', 'Londen': 'GB',
    'Ljubljana': 'SI', 'Busan': 'KR', 'Seoul': 'KR', 'Reykjavík': 'IS',
}


def clean_text(value, separator=' '):
    if not value:
        return ''
    text = value.get_text(separator, strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    if separator == '\n':
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r' *\n *', '\n', text)
        return re.sub(r'\n{3,}', '\n\n', text).strip()
    return re.sub(r'\s+', ' ', text).strip()


def fetch(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.text


def listing_urls(session):
    """Return all performances retained by the public agenda (including past ones)."""
    urls = set()
    offset = 0
    while True:
        html = fetch(session, AGENDA_URL, {
            'start': '1900-01-01', 'offset': offset, 'limit': 20, 'locale': 'nl',
        })
        soup = BeautifulSoup(html, 'html.parser')
        page_urls = {
            urljoin(SOURCE_URL, link['href'])
            for link in soup.select('.concert-overview__results-item a[href*="/nl/agenda/"]')
            if re.search(r'-\d{4}-\d{2}-\d{2}/?(?:\?|$)', link.get('href', ''))
        }
        new_urls = page_urls - urls
        urls.update(page_urls)
        if not page_urls or not new_urls:
            break
        next_offset = offset + 20
        next_pattern = re.compile(rf'[?&]offset={next_offset}(?:&|$)')
        if not any(next_pattern.search(link.get('href', '')) for link in soup.select('a[href]')):
            break
        offset = next_offset
    return sorted(urls)


def parse_location(value):
    value = clean_text(value)
    if not value:
        return None
    country_code = 'NL'
    location = value
    if ' - ' in value:
        possible_location, country_name = value.rsplit(' - ', 1)
        explicit_country = COUNTRIES.get(country_name.strip())
        if explicit_country:
            location, country_code = possible_location, explicit_country
    if ',' not in location:
        return None
    venue, city = [part.strip() for part in location.rsplit(',', 1)]
    # A few older tour records repeat the venue after the city.
    city = city.split(' - ', 1)[0].strip()
    inferred_country = next(
        (code for marker, code in CITY_COUNTRIES.items() if marker.casefold() in city.casefold()),
        None,
    )
    country_code = inferred_country or country_code
    if not venue or not city:
        return None
    return venue, city, country_code


def parse_detail(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title = clean_text(soup.select_one('h1.page-header__title'))
    date_match = re.search(r'-(\d{4}-\d{2}-\d{2})/?(?:\?|$)', url)
    if not title or not date_match:
        return None
    try:
        event_date = date.fromisoformat(date_match.group(1)).isoformat()
    except ValueError:
        return None

    details = soup.select_one('.activity-details')
    detail_items = details.select('.activity-details__item') if details else []
    location_node = detail_items[-1] if detail_items else None
    location = parse_location(location_node)
    if not location:
        return None
    venue, city, country_code = location

    start_time = None
    for node in details.select('.activity-details__time') if details else []:
        match = re.search(r'Aanvang\s+(\d{1,2}):(\d{2})', clean_text(node), re.I)
        if match:
            start_time = f'{int(match.group(1)):02d}:{match.group(2)}'
            break

    description_parts = []
    intro = clean_text(soup.select_one('.concert-detail__article > p'), '\n')
    body = clean_text(soup.select_one('.expandable__content .richtext'), '\n')
    program = clean_text(soup.select_one('.program'), '\n')
    for part in (intro, body, program):
        if part and part not in description_parts:
            description_parts.append(part)

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': start_time,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': '\n\n'.join(description_parts) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_detail(future.result(), url)
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Concertgebouworkest concert detail',
                    event='crawler_item_failed', level='warning', url=url,
                    error_type=type(error).__name__, error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['url']
    ))


class ConcertgebouworkestNlCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='concertgebouworkest_nl',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='NL',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from'],
    )

    def scrape(self):
        return get_concerts()


def main():
    ConcertgebouworkestNlCrawler().run()


if __name__ == '__main__':
    main()
