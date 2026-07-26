import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://bachcollegium.cz'
LISTING_URL = f'{BASE_URL}/koncerty'
SOURCE = 'Bach Collegium Praha'
SOURCE_URL = 'https://www.bachcollegium.cz/'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def clean_text(text):
    if not text:
        return ''

    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    # The server does not declare a charset and requests otherwise assumes
    # ISO-8859-1, although the pages are UTF-8.
    response.encoding = 'utf-8'
    return BeautifulSoup(response.text, 'html.parser')


def find_concert_links(session):
    soup = get_soup(session, LISTING_URL)
    links = []

    for link in soup.select('article.article h3 a[href]'):
        url = urljoin(BASE_URL, link.get('href', '').strip())
        if url.startswith(f'{BASE_URL}/koncerty/'):
            links.append(url)

    return list(dict.fromkeys(links))


def extract_location(text):
    city = None
    # Check Filipov first because that event also names the Prague-based
    # ensemble among its performers.
    if re.search(r'\bFilipov\b', text, re.IGNORECASE):
        city = 'Filipov'
    elif re.search(r'\bPraha(?:\s+\d+)?\b', text, re.IGNORECASE):
        city = 'Praha'

    venue_match = re.search(
        r'\b((?:kostel|bazilika|chrám|kaple)\b[^,\n|]+)',
        text,
        re.IGNORECASE,
    )
    venue = clean_text(venue_match.group(1)) if venue_match else None
    if venue:
        venue = venue[0].upper() + venue[1:]

    return venue, city


def extract_concert(session, url):
    soup = get_soup(session, url)
    title_element = soup.select_one('#content > h1')
    time_element = soup.select_one('.articleBody time.date[datetime]')
    body = soup.select_one('.articleBody')

    if not title_element or not time_element or not body:
        return None

    try:
        starts_at = datetime.strptime(
            time_element['datetime'].strip(),
            '%Y-%m-%d %H:%M:%S',
        )
    except (KeyError, ValueError):
        return None

    description_body = BeautifulSoup(str(body), 'html.parser')
    for unwanted in description_body.select(
        '.dateRange, .articleDetImg, script, style, img'
    ):
        unwanted.decompose()

    description = clean_text(description_body.get_text('\n', strip=True))
    location_text = clean_text(body.get_text('\n', strip=True))
    venue, city = extract_location(location_text)

    return {
        'title': clean_text(title_element.get_text(' ', strip=True)),
        'date': starts_at.strftime('%Y-%m-%d'),
        'url': url,
        'time_from': starts_at.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'description': description or None,
    }


def get_concerts():
    session = requests.Session()
    concerts = []

    for url in find_concert_links(session):
        concert = extract_concert(session, url)
        if concert:
            concerts.append(concert)

    return concerts


class BachCollegiumCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bachcollegium_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'venue',
            'city',
            'description',
        ],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    BachCollegiumCrawler().run()


if __name__ == '__main__':
    main()
