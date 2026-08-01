import re
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.kfpar.cz'
LISTING_URL = f'{BASE_URL}/nejblizsi-koncerty'
SOURCE = 'Komorní filharmonie Pardubice'
SOURCE_URL = 'https://www.kfpar.cz/'
DEFAULT_CITY = 'Pardubice'
DEFAULT_COUNTRY_CODE = 'CZ'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def clean_text(value):
    text = unescape(value or '').replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or 'utf-8'
    return BeautifulSoup(response.text, 'html.parser')


def parse_date(value):
    match = re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\b', value)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    return f'{year:04d}-{month:02d}-{day:02d}'


def parse_time(value):
    match = re.search(r'\b(\d{1,2}):(\d{2})\b', value)
    if not match:
        match = re.search(r'\bv\s+(\d{1,2})\s+hodin', value, re.IGNORECASE)
    return f'{int(match.group(1)):02d}:{int(match.group(2) if match.lastindex > 1 else 0):02d}' if match else None


def find_concert_links(session):
    links = []
    for page in range(1, 101):
        url = LISTING_URL if page == 1 else f'{LISTING_URL}?page={page}'
        soup = get_soup(session, url)
        page_links = []
        for link in soup.select('a[href]'):
            href = urljoin(BASE_URL, link.get('href', '').strip())
            path = urlparse(href).path
            if href.startswith(BASE_URL) and path not in ('/', '/nejblizsi-koncerty') and link.get_text(strip=True) == 'Podrobnosti':
                page_links.append(href)
        new_links = [link for link in page_links if link not in links]
        links.extend(new_links)
        if not new_links or not soup.select_one(f'a[href*="page={page + 1}"]'):
            break
    return links


def extract_concert(session, url):
    soup = get_soup(session, url)
    title_element = soup.select_one('h1') or soup.select_one('.cNews__title')
    title = clean_text(title_element.get_text(' ', strip=True)) if title_element else None
    date_element = soup.select_one('.cNews__date')
    date_text = clean_text(date_element.get_text(' ', strip=True)) if date_element else clean_text(soup.get_text(' ', strip=True))
    date = parse_date(date_text)
    body = soup.select_one('.cText__text')
    description = clean_text(body.get_text('\n', strip=True)) if body else None
    if not title or not date or not description:
        return None
    time_from = parse_time(description)
    venue = DEFAULT_CITY
    venue_match = re.search(r'([^\n]+(?:síň|sál|divadlo|kostel)[^\n]*)', description, re.IGNORECASE)
    if venue_match:
        venue = clean_text(venue_match.group(1))
    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': venue,
        'city': DEFAULT_CITY,
        'country_code': DEFAULT_COUNTRY_CODE,
        'description': description,
        'type': 'concert',
    }


def get_concerts():
    session = requests.Session()
    return [concert for url in find_concert_links(session) if (concert := extract_concert(session, url))]


class KfparCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='kfpar_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue', 'city', 'country_code', 'description', 'type'],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
    )

    def scrape(self):
        return get_concerts()


def main():
    KfparCrawler().run()


if __name__ == '__main__':
    main()
