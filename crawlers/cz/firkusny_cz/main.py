import re
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://firkusny.cz'
PROGRAM_URL = f'{BASE_URL}/program/'
SOURCE = 'Klavírní festival Rudolfa Firkušného'
SOURCE_URL = BASE_URL
DEFAULT_CITY = 'Praha'

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
    text = re.sub(r'\n+([:;,.])', r'\1', text)
    text = re.sub(r'\n+([!?])', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def find_concert_links(session):
    soup = get_soup(session, PROGRAM_URL)
    links = []

    for link in soup.select('a[href*="/program/"]'):
        href = link.get('href', '').strip()
        url = urljoin(BASE_URL, href)
        if url.rstrip('/') == PROGRAM_URL.rstrip('/'):
            continue
        if not re.match(rf'{BASE_URL}/program/[^/#?]+/?$', url):
            continue
        links.append(url)

    return list(dict.fromkeys(links))


def parse_date(soup):
    date_el = soup.select_one('.event__date-val')
    date_text = clean_text(date_el.get_text(' ', strip=True)) if date_el else ''
    match = re.search(r'\b(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(20\d{2})\b', date_text)
    if not match:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    return f'{year}-{month:02d}-{day:02d}'


def parse_time(soup):
    time_el = soup.select_one('.event__date-daytime')
    time_text = clean_text(time_el.get_text(' ', strip=True)) if time_el else ''
    match = re.search(r'\b(\d{1,2})[.:](\d{2})\b', time_text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    return f'{hour:02d}:{minute:02d}'


def extract_text_from_selector(soup, selector):
    el = soup.select_one(selector)
    if not el:
        return ''
    return clean_text(el.get_text('\n', strip=True))


def extract_description(soup):
    parts = []

    for selector in [
        '.event__desc',
        '.event__repertoire',
        '.event__interprets',
        '.event-blocks',
    ]:
        text = extract_text_from_selector(soup, selector)
        if text:
            parts.append(text)

    description = clean_text('\n\n'.join(parts))
    return description or None


def extract_concert(session, url):
    soup = get_soup(session, url)

    title = extract_text_from_selector(soup, '.event__title')
    if not title and soup.title:
        title = clean_text(soup.title.get_text(' ', strip=True).split(' – ', 1)[0])

    date = parse_date(soup)
    if not title or not date:
        return None

    venue = extract_text_from_selector(soup, '.event__place-name') or extract_text_from_selector(soup, '.event__place')
    description = extract_description(soup)

    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': parse_time(soup),
        'time_to': None,
        'venue': venue or None,
        'city': DEFAULT_CITY,
        'description': description,
        'type': 'concert',
    }


class FirkusnyCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='firkusny_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'time_to',
            'venue',
            'city',
            'description',
            'type',
        ],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        session = requests.Session()
        concerts = []

        for link in find_concert_links(session):
            concert = extract_concert(session, link)
            if concert:
                concerts.append(concert)

        return concerts


def main():
    FirkusnyCrawler().run()


if __name__ == '__main__':
    main()
