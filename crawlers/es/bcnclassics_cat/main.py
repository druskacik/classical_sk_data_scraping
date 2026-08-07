import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.bcnclassics.cat/es'
PROGRAM_URL = f'{SOURCE_URL}/programacion'
SOURCE = 'BCN Clàssics'
CITY = 'Barcelona'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'es-ES,es;q=0.9,ca;q=0.8',
}

MONTHS = {
    'enero': 1,
    'febrero': 2,
    'marzo': 3,
    'abril': 4,
    'mayo': 5,
    'junio': 6,
    'julio': 7,
    'agosto': 8,
    'septiembre': 9,
    'setiembre': 9,
    'octubre': 10,
    'noviembre': 11,
    'diciembre': 12,
}


def clean_text(element):
    if not element:
        return ''
    text = element.get_text('\n', strip=True) if hasattr(element, 'get_text') else str(element)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def season_ids(soup):
    values = []
    for option in soup.select('select[name="t"] option[value]'):
        value = option.get('value', '')
        if value.isdigit() and value != '0' and value not in values:
            values.append(value)
    return values


def concert_urls(session):
    first_page = get_soup(session, PROGRAM_URL)
    seasons = season_ids(first_page)
    pages = [first_page]
    for season in seasons[1:]:
        try:
            pages.append(get_soup(session, f'{PROGRAM_URL}/{season}/0/0'))
        except requests.RequestException as error:
            log_message(
                'Failed to scrape programme season',
                event='crawler_page_failed',
                level='warning',
                url=f'{PROGRAM_URL}/{season}/0/0',
                error_type=type(error).__name__,
                error_message=str(error),
            )

    urls = set()
    for page in pages:
        for link in page.select('a[href*="/concierto/"][href]'):
            urls.add(urljoin(SOURCE_URL, link['href']))
    return sorted(urls)


def parse_datetime(value):
    normalized = clean_text(value).lower()
    match = re.search(
        r'(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})'
        r'(?:\s*\|\s*(\d{1,2}):(\d{2})\s*h?)?',
        normalized,
    )
    if not match or match.group(2) not in MONTHS:
        return None, None
    try:
        concert_date = date(
            int(match.group(3)), MONTHS[match.group(2)], int(match.group(1))
        ).isoformat()
    except ValueError:
        return None, None
    time_from = None
    if match.group(4):
        hour, minute = int(match.group(4)), int(match.group(5))
        if hour < 24 and minute < 60:
            time_from = f'{hour:02d}:{minute:02d}'
    return concert_date, time_from


def parse_concert(soup, url):
    title = clean_text(soup.select_one('h1'))
    concert_date, time_from = parse_datetime(soup.select_one('.date'))
    venue = clean_text(soup.select_one('.location h6') or soup.select_one('.location'))
    venue_parts = [part.strip() for part in venue.split(' - ') if part.strip()]
    if venue_parts and len(set(venue_parts)) == 1:
        venue = venue_parts[0]
    description = clean_text(soup.select_one('.concert-descripcion')) or None
    if not title or not concert_date or not venue:
        return None
    return {
        'title': title,
        'date': concert_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': CITY,
        'country_code': 'ES',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = concert_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_concert(future.result(), url)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class BcnclassicsCatCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bcnclassics_cat',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='ES',
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
    BcnclassicsCatCrawler().run()


if __name__ == '__main__':
    main()
