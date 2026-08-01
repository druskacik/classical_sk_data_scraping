import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.jfcb.cz'
SOURCE_URL = 'https://www.jfcb.cz/'
SOURCE = 'Jihočeská filharmonie'
LISTING_URL = f'{BASE_URL}/koncerty'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36',
}


def clean_text(value):
    value = unescape(value or '').replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or 'utf-8'
    return BeautifulSoup(response.text, 'html.parser')


def parse_datetime(value):
    match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})', value)
    if not match:
        return None, None
    day, month, year, hour, minute = map(int, match.groups())
    return f'{year:04d}-{month:02d}-{day:02d}', f'{hour:02d}:{minute:02d}'


def city_from_venue(venue):
    if not venue:
        return None
    if 'České Budějovice' in venue or 'Kostel sv. Anny' in venue or 'Metropol' in venue:
        return 'České Budějovice'
    if 'Český Krumlov' in venue:
        return 'Český Krumlov'
    if 'Vídeň' in venue or 'Schönbrunn' in venue:
        return 'Vídeň'
    return None


def find_concert_urls(session):
    soup = get_soup(session, LISTING_URL)
    urls = []
    for link in soup.select('a[href^="/koncerty/"]'):
        url = urljoin(BASE_URL, link['href'])
        if url not in urls:
            urls.append(url)
    return urls


def extract_concert(session, url):
    soup = get_soup(session, url)
    title_el = soup.select_one('h1')
    date_el = soup.select_one('.datum-a-cas')
    venue_el = soup.select_one('.opacity-text')
    if not title_el or not date_el:
        return None

    date, time_from = parse_datetime(clean_text(date_el.get_text(' ', strip=True)))
    if not date:
        return None
    venue = clean_text(venue_el.get_text(' ', strip=True)) if venue_el else None

    description_parts = []
    for block in soup.select('.rich-text.w-richtext'):
        text = clean_text(block.get_text('\n', strip=True))
        if text and text not in description_parts:
            description_parts.append(text)
    description = '\n\n'.join(description_parts) or None

    return {
        'title': clean_text(title_el.get_text(' ', strip=True)),
        'date': date,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': venue,
        'city': city_from_venue(venue),
        'description': description,
        'type': 'concert',
    }


def get_concerts():
    session = requests.Session()
    return [concert for url in find_concert_urls(session) if (concert := extract_concert(session, url))]


class JcFilharmonieCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='jcfilharmonie_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue', 'city', 'description', 'type'],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
    )

    def scrape(self):
        return get_concerts()


def main():
    JcFilharmonieCrawler().run()


if __name__ == '__main__':
    main()
