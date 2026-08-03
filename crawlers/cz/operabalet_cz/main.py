import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.operabalet.cz/'
PROGRAM_URL = urljoin(SOURCE_URL, 'program/')
SOURCE = 'Divadlo města Ústí nad Labem'
HOME_CITY = 'Ústí nad Labem'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'cs-CZ,cs;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    value = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def parse_datetime(value):
    match = re.search(
        r'(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s+(\d{1,2}):(\d{2})',
        clean_text(value),
    )
    if not match:
        return None, None
    day, month, year, hour, minute = map(int, match.groups())
    try:
        event_date = date(year, month, day).isoformat()
    except ValueError:
        return None, None
    if hour > 23 or minute > 59:
        return None, None
    return event_date, f'{hour:02d}:{minute:02d}'


def node_text(parent, selector):
    node = parent.select_one(selector)
    return clean_text(node.get_text('\n', strip=True)) if node else ''


def resolve_location(value):
    venue = clean_text(value)
    if not venue:
        return None, None

    # A touring label identifies a city but not an actual venue. Returning it
    # as a hall would create a misleading record, so such performances are
    # skipped unless the site later starts publishing a concrete tour venue.
    if re.search(r'\bZ[ÁA]JEZD\b', venue, re.IGNORECASE):
        return None, None
    return venue, HOME_CITY


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def detail_description(session, url):
    soup = get_soup(session, url)
    parts = []
    for selector in ('.program-description', '.program-details'):
        node = soup.select_one(selector)
        if not node:
            continue
        for unwanted in node.select('script, style, form, img'):
            unwanted.decompose()
        text = clean_text(node.get_text('\n', strip=True))
        if text:
            parts.append(text)
    return clean_text('\n\n'.join(parts)) or None


def listing_records(soup):
    records = []
    for item in soup.select('.itemlistes .itemlist'):
        link = item.select_one('.detailbuy a[href]')
        title = node_text(item, 'h3')
        event_date, time_from = parse_datetime(node_text(item, '.date'))
        venue, city = resolve_location(node_text(item, '.location'))
        if not link or not title or not event_date or not venue or not city:
            continue

        description_node = item.select_one('.text > p:not(.date):not(.location):not(.price)')
        description = (
            clean_text(description_node.get_text('\n', strip=True))
            if description_node else None
        ) or None
        records.append(
            {
                'title': title,
                'date': event_date,
                'url': urljoin(SOURCE_URL, link.get('href')),
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': 'CZ',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = listing_records(get_soup(session, PROGRAM_URL))

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(detail_description, session, record['url']): record
            for record in records
        }
        for future in as_completed(futures):
            record = futures[future]
            try:
                record['description'] = future.result() or record['description']
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
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


class OperabaletCzCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='operabalet_cz',
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
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    OperabaletCzCrawler().run()


if __name__ == '__main__':
    main()
