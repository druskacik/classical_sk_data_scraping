import re
from datetime import date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.mhflj.cz/'
PROGRAM_URL = urljoin(BASE_URL, 'program/')
SOURCE_NAME = 'Mezinárodní hudební festival Leoše Janáčka'

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
    value = unescape(value).replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def fetch_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_date(value):
    match = re.search(r'(\d{1,2})\s*/\s*(\d{1,2})\s*[—-]\s*(20\d{2})', clean_text(value))
    if not match:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1))).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2}):(\d{2})\b', clean_text(value))
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def split_place(value):
    value = clean_text(value)
    if not value:
        return None, None

    venue, separator, city = value.rpartition(',')
    if separator and venue.strip() and city.strip():
        return venue.strip(), city.strip()
    return value, None


def element_text(soup, selector):
    element = soup.select_one(selector)
    return clean_text(element.get_text('\n', strip=True)) if element else ''


def extract_description(soup):
    parts = []
    for block in soup.select('.post_text_inner .wpb_text_column > .wpb_wrapper'):
        for removable in block.select('button, script, style, noscript'):
            removable.decompose()
        text = clean_text(block.get_text('\n', strip=True))
        if text and text.lower() not in {'vyprodáno', 'zrušeno', 'rezervace'}:
            parts.append(text)
    return clean_text('\n\n'.join(parts)) or None


def extract_detail(session, url):
    soup = fetch_soup(session, url)
    title_element = soup.select_one('.entry_title')
    if title_element:
        for removable in title_element.select('.date, meta'):
            removable.decompose()

    place = element_text(soup, '.concert-detail-place')
    venue, city = split_place(place)
    return {
        'title': clean_text(title_element.get_text(' ', strip=True)) if title_element else None,
        'date': parse_date(element_text(soup, '.concert-detail-date')),
        'time_from': parse_time(element_text(soup, '.concert-detail-hour')),
        'venue': venue,
        'city': city,
        'description': extract_description(soup),
    }


def extract_listing(session):
    soup = fetch_soup(session, PROGRAM_URL)
    records = []

    for item in soup.select('.concert-container .concert-item'):
        link = item.select_one('a[href]')
        if not link:
            continue

        url = urljoin(PROGRAM_URL, link.get('href'))
        place = element_text(item, '.concert-place')
        venue, city = split_place(place)
        records.append({
            'title': element_text(item, '.concert-name') or None,
            'date': parse_date(element_text(item, '.concert-date')),
            'url': url,
            'time_from': parse_time(element_text(item, '.concert-hour')),
            'venue': venue,
            'city': city,
            'country_code': 'CZ',
            'description': element_text(item, '.concert-perex') or None,
            'source_url': BASE_URL,
            'source': SOURCE_NAME,
        })

    return records


class MhfljCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mhflj_cz',
        source=SOURCE_NAME,
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
        dedupe_subset=['title', 'date', 'time_from', 'url'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)

        records = extract_listing(session)
        details = {}
        for record in records:
            url = record['url']
            if url not in details:
                try:
                    details[url] = extract_detail(session, url)
                except requests.RequestException as exc:
                    print(f'Failed to scrape concert detail {url}: {exc}')
                    details[url] = {}

            for field, value in details[url].items():
                if value:
                    record[field] = value

        unique = {}
        for record in records:
            if record.get('title') and record.get('date'):
                key = (record['title'], record['date'], record.get('time_from'), record['url'])
                unique[key] = record
        return list(unique.values())


def main():
    concerts = MhfljCrawler().scrape()
    print(f'Found {len(concerts)} concerts')
    for concert in concerts:
        print(concert)
    return concerts


if __name__ == '__main__':
    main()
