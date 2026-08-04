import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://shf.cz/'
PROGRAM_URL = urljoin(SOURCE_URL, 'program/')
ARCHIVE_URL = urljoin(SOURCE_URL, 'archiv/')
SOURCE = 'Svatováclavský hudební festival'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        'Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'cs-CZ,cs;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    value = str(value).replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def canonical_url(value):
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def archive_years(session):
    soup = get_soup(session, ARCHIVE_URL)
    years = set()
    for link in soup.select('a[href*="specific_year="]'):
        values = parse_qs(urlsplit(link.get('href', '')).query).get('specific_year', [])
        if values and re.fullmatch(r'20\d{2}', values[0]):
            years.add(int(values[0]))

    # The live page can precede an archive-menu update during a new season.
    years.add(date.today().year)
    return sorted(years, reverse=True)


def parse_city(location):
    city_element = location.find('b')
    city = clean_text(city_element.get_text(' ', strip=True) if city_element else '')
    if not city:
        return None
    if re.match(r'^ostrava(?:\s*[-–—].*)?$', city, re.IGNORECASE):
        return 'Ostrava'
    return city


def parse_venue(location):
    city_element = location.find('b')
    if not city_element:
        return None
    parts = [clean_text(text) for text in location.stripped_strings]
    if parts:
        parts.pop(0)
    venue = clean_text(' '.join(part for part in parts if part))
    return venue or None


def parse_listing_card(card, year):
    title_link = card.select_one('.event-item-title a[href]')
    day_element = card.select_one('.event-item-date-day')
    month_element = card.select_one('.event-item-date-month')
    location = card.select_one('.event-item-location-time .location')
    if not title_link or not day_element or not month_element or not location:
        return None

    try:
        event_date = date(
            year,
            int(clean_text(month_element.get_text())),
            int(clean_text(day_element.get_text())),
        ).isoformat()
    except ValueError:
        return None

    title = clean_text(title_link.get_text(' ', strip=True))
    city = parse_city(location)
    venue = parse_venue(location)
    if not title or not city or not venue:
        return None

    time_element = card.select_one('.event-item-location-time .time strong')
    time_match = re.search(
        r'\b([01]?\d|2[0-3]):([0-5]\d)\b',
        clean_text(time_element.get_text()) if time_element else '',
    )
    return {
        'title': title,
        'date': event_date,
        'url': canonical_url(urljoin(PROGRAM_URL, title_link.get('href'))),
        'time_from': f'{int(time_match.group(1)):02d}:{time_match.group(2)}' if time_match else None,
        'venue': venue,
        'city': city,
        'country_code': 'CZ',
        'description': None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def listing_records(session, year):
    soup = get_soup(session, f'{PROGRAM_URL}?specific_year={year}')
    records = []
    for card in soup.select('.archive-event-item-col .event-item'):
        record = parse_listing_card(card, year)
        if record:
            records.append(record)
    return records


def detail_description(url):
    session = requests.Session()
    session.headers.update(HEADERS)
    soup = get_soup(session, url)
    sections = soup.select('.single-event-program, .single-event-interprets')
    description = clean_text('\n\n'.join(section.get_text('\n', strip=True) for section in sections))
    return description or None


def add_descriptions(records):
    by_url = {}
    for record in records:
        by_url.setdefault(record['url'], []).append(record)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(detail_description, url): url for url in by_url}
        for future in as_completed(futures):
            url = futures[future]
            try:
                description = future.result()
                for record in by_url[url]:
                    record['description'] = description
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )


class ShfCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='shf_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
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
        dedupe_subset=['title', 'date', 'url'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        records = []
        for year in archive_years(session):
            try:
                records.extend(listing_records(session, year))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape programme year',
                    event='crawler_page_failed',
                    level='warning',
                    url=f'{PROGRAM_URL}?specific_year={year}',
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
        add_descriptions(records)
        return records


def main():
    ShfCrawler().run()


if __name__ == '__main__':
    main()
