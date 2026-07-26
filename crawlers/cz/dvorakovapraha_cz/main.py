import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.dvorakovapraha.cz'
PROGRAM_URL = f'{BASE_URL}/program'
SOURCE = 'Dvořákova Praha'
DEFAULT_CITY = 'Praha'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'cs,en;q=0.8',
}


def clean_text(value):
    if not value:
        return ''
    value = unescape(value).replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[ \t\r\f\v]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\s+([,.;:!?])', r'\1', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or 'utf-8'
    return BeautifulSoup(response.text, 'html.parser')


def canonical_url(href):
    absolute = urljoin(BASE_URL, href)
    parts = urlsplit(absolute)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))


def discover_concert_urls(session):
    soup = get_soup(session, PROGRAM_URL)
    urls = []

    for link in soup.select('.cms_card_program_item a[href], a[href^="/program/"]'):
        href = link.get('href', '').strip()
        url = canonical_url(href)
        if re.fullmatch(rf'{re.escape(BASE_URL)}/program/[^/]+', url):
            urls.append(url)

    return list(dict.fromkeys(urls))


def labelled_value(soup, label):
    normalized_label = clean_text(label).casefold()
    container = soup.select_one('.program_data_content_width')
    if not container:
        return None

    for row in container.select('.margin-bottom'):
        elements = row.find_all(['p', 'a'], recursive=True)
        if not elements:
            continue
        row_label = clean_text(elements[0].get_text(' ', strip=True)).casefold()
        if row_label != normalized_label:
            continue

        for element in elements[1:]:
            value = clean_text(element.get_text(' ', strip=True))
            if value and value.casefold() != normalized_label:
                return value
    return None


def parse_date(value):
    if not value:
        return None
    match = re.search(r'\b\d{1,2}/\d{1,2}/\d{4}\b', value)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(), '%d/%m/%Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    if not value:
        return None
    match = re.search(r'\b([01]?\d|2[0-3])[.:](\d{2})\b', value)
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def extract_description(soup):
    parts = []
    for selector in [
        '.section_concert_program',
        '.section_interpreti',
        '.section_concert_anotace',
    ]:
        section = soup.select_one(selector)
        if not section:
            continue
        text = clean_text(section.get_text('\n', strip=True))
        if text:
            parts.append(text)
    return clean_text('\n\n'.join(parts)) or None


def extract_concert(session, url):
    soup = get_soup(session, url)
    title_element = soup.select_one('h1.headline_hero_calc, h1')
    title = clean_text(title_element.get_text(' ', strip=True)) if title_element else ''
    date = parse_date(labelled_value(soup, 'Datum'))

    if not title or not date:
        return None

    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': parse_time(labelled_value(soup, 'Čas')),
        'venue': labelled_value(soup, 'Místo'),
        'city': DEFAULT_CITY,
        'country_code': 'CZ',
        'description': extract_description(soup),
        'source_url': BASE_URL,
        'source': SOURCE,
    }


class DvorakovaPrahaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='dvorakovapraha_cz',
        source=SOURCE,
        source_url=BASE_URL,
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
        dedupe_subset=['title', 'date', 'time_from'],
    )

    def scrape(self):
        session = requests.Session()
        concerts = []

        for url in discover_concert_urls(session):
            try:
                concert = extract_concert(session, url)
            except requests.RequestException as exc:
                print(f'Failed to scrape {url}: {exc}')
                continue
            if concert:
                concerts.append(concert)
            else:
                print(f'Skipping {url}: missing title or date')

        return concerts


def main():
    DvorakovaPrahaCrawler().run()


if __name__ == '__main__':
    main()
