import re
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://auditeorganum.cz'
SOURCE = 'Svatojakubské Audite Organum'
SOURCE_URL = BASE_URL
DEFAULT_CITY = 'Praha'
DEFAULT_VENUE = 'Bazilika sv. Jakuba'

CATEGORY_URLS = [
    f'{BASE_URL}/category/koncerty/',
    f'{BASE_URL}/category/festival/',
]

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
    return BeautifulSoup(response.text, 'html.parser')


def find_concert_links(session):
    links = []

    for category_url in CATEGORY_URLS:
        soup = get_soup(session, category_url)
        for link in soup.select('article a[href]'):
            href = link.get('href', '').strip()
            if not re.match(rf'{BASE_URL}/(?:koncerty|festival)/', href):
                continue
            links.append(urljoin(BASE_URL, href))

    return list(dict.fromkeys(links))


def parse_date(text):
    match = re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\b', text)
    if not match:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    return f'{year}-{month:02d}-{day:02d}'


def parse_time(text):
    match = re.search(r'\b(?:v|od)\s*(\d{1,2})[.:]\s*(\d{2})\s*(?:hodin|h\b)', text, re.IGNORECASE)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    return f'{hour:02d}:{minute:02d}'


def is_date_range(text):
    return bool(
        re.search(
            r'\bOD\s+\d{1,2}\.\s*\d{1,2}\.\s*[–-]\s*\d{1,2}\.\s*\d{1,2}\.\s*20\d{2}\b',
            text,
            re.IGNORECASE,
        )
    )


def parse_title(raw_title):
    title = clean_text(raw_title)
    title = re.sub(r'\s*[–-]\s*\d{1,2}\.\s*\d{1,2}\.\s*20\d{2}.*$', '', title)
    title = re.sub(r'\s*[–-]\s*OD\s+\d{1,2}\.\s*\d{1,2}\..*$', '', title, flags=re.IGNORECASE)
    return clean_text(title) or SOURCE


def extract_description(entry_content, title):
    if not entry_content:
        return None

    content = BeautifulSoup(str(entry_content), 'html.parser')

    for tag in content.select('script, style, img'):
        tag.decompose()

    for link in content.select('a'):
        link_text = clean_text(link.get_text(' ', strip=True))
        href = link.get('href', '')
        if 'kudyznudy.cz' in href or link_text.lower() == 'koupit vstupenku':
            link.decompose()

    body = clean_text(content.get_text('\n', strip=True))
    description = clean_text('\n\n'.join(part for part in [title, body] if part))
    return description or None


def extract_concert(session, url):
    soup = get_soup(session, url)

    title_el = soup.select_one('#post-title') or soup.select_one('.entry-title')
    raw_title = clean_text(title_el.get_text(' ', strip=True)) if title_el else ''
    if not raw_title and soup.title:
        raw_title = clean_text(soup.title.get_text(' ', strip=True).split(' - ', 1)[0])

    if is_date_range(raw_title):
        return None

    date = parse_date(raw_title)
    time_from = parse_time(raw_title)
    if not date:
        return None

    title = parse_title(raw_title)
    entry_content = soup.select_one('.entry-content')
    description = extract_description(entry_content, title)

    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': DEFAULT_VENUE,
        'city': DEFAULT_CITY,
        'description': description,
        'type': 'concert',
    }


def get_concerts():
    session = requests.Session()
    concert_links = find_concert_links(session)
    concerts = []

    for link in concert_links:
        concert = extract_concert(session, link)
        if concert:
            concerts.append(concert)

    return concerts


class AuditeOrganumCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='auditeorganum_cz',
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
        return get_concerts()


def main():
    AuditeOrganumCrawler().run()


if __name__ == '__main__':
    main()
