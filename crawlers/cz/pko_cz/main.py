import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


ORIGINAL_URL = 'https://www.pko.cz/'
BASE_URL = 'https://www.pko.cz'
LISTING_URL = f'{BASE_URL}/en/current-season/'
SOURCE_NAME = 'Prague Chamber Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    value = value.replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def parse_datetime(value):
    match = re.search(
        r'(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+from\s+(\d{1,2}:\d{2}))?',
        value or '',
        re.IGNORECASE,
    )
    if not match:
        return None, None

    day, month, year, time_from = match.groups()
    try:
        date = datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None, None
    return date, time_from


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def element_text(parent, selector):
    element = parent.select_one(selector)
    return clean_text(element.get_text(' ', strip=True)) if element else None


def listing_description(section):
    lines = []
    for item in section.select('.fl li'):
        text = clean_text(item.get_text(' ', strip=True))
        if text:
            lines.append(text)
    return clean_text('\n'.join(lines)) or None


def detail_description(session, url):
    soup = get_soup(session, url)
    content = soup.select_one('article.concert .entry-content')
    if not content:
        return None

    for element in content.select(
        'script, style, form, img, .buy-button, .datum, .location, h1'
    ):
        element.decompose()
    return clean_text(content.get_text('\n', strip=True)) or None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    soup = get_soup(session, LISTING_URL)
    concerts = []

    for section in soup.select('main .page-content section'):
        link = section.select_one('h3.season a[href*="/concert/"]')
        if not link:
            continue

        date_text = element_text(section, '.fl > p')
        date, time_from = parse_datetime(date_text)
        if not date:
            continue

        url = urljoin(BASE_URL, link.get('href'))
        venue_link = section.select_one('.fl > p a[href*="/location/"]')
        venue = clean_text(venue_link.get_text(' ', strip=True)) if venue_link else None
        description = listing_description(section)
        try:
            description = detail_description(session, url) or description
        except requests.RequestException as exc:
            log_message('Failed to scrape concert detail', event='crawler_item_failed', level='warning', url=url, error_type=type(exc).__name__, error_message=str(exc))

        concerts.append(
            {
                'title': clean_text(link.get_text(' ', strip=True)),
                'date': date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': 'Prague',
                'country_code': 'CZ',
                'description': description,
                'source_url': ORIGINAL_URL,
                'source': SOURCE_NAME,
            }
        )

    return concerts


class PkoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='pko_cz',
        source=SOURCE_NAME,
        source_url=ORIGINAL_URL,
        country_code='CZ',
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
        return get_concerts()


def main():
    PkoCrawler().run()


if __name__ == '__main__':
    main()
