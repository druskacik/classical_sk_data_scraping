import re
from datetime import date
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.filharmonie-zlin.cz'
SOURCE_URL = f'{BASE_URL}/'
SOURCE = 'Filharmonie Bohuslava Martinů'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ClassicalBot/1.0)'}


def clean_text(value):
    value = unescape(value or '').replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or 'utf-8'
    return BeautifulSoup(response.text, 'html.parser')


def month_values(start, count=18):
    year, month = start.year, start.month
    for _ in range(count):
        yield year, month
        month += 1
        if month == 13:
            year, month = year + 1, 1


def find_event_links(session):
    links = []
    for year, month in month_values(date.today()):
        soup = get_soup(session, f'{BASE_URL}/kalendar?date={year}-{month}')
        for anchor in soup.select('table a[href]'):
            href = urljoin(BASE_URL, anchor['href'])
            if urlparse(href).netloc == urlparse(BASE_URL).netloc and re.search(r'/\d+a-', href):
                links.append(href)
    return list(dict.fromkeys(links))


def parse_datetime(text):
    match = re.search(
        r'(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2}).*?'
        r'(\d{1,2})(?:[.:](\d{2}))?\s*hodin',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, None
    day, month, year, hour, minute = (int(value or 0) for value in match.groups())
    return f'{year:04d}-{month:02d}-{day:02d}', f'{hour:02d}:{minute:02d}'


def extract_event(session, url):
    soup = get_soup(session, url)
    main = soup.select_one('main') or soup
    title_node = main.select_one('h1')
    header = main.select_one('h1')
    date_text = header.find_previous('p').get_text(' ', strip=True) if header else ''
    title = clean_text(title_node.get_text(' ', strip=True)) if title_node else None
    event_date, time_from = parse_datetime(date_text)
    venue_node = main.find(string=re.compile(r'Místo konání', re.I))
    venue = None
    if venue_node:
        venue = clean_text(venue_node.parent.get_text(' ', strip=True)).split(':', 1)[-1].strip(' |')
    paragraphs = []
    for node in main.select('p'):
        text = clean_text(node.get_text(' ', strip=True))
        if text and text != date_text and not text.startswith('Místo konání'):
            paragraphs.append(text)
    description = clean_text('\n\n'.join([title or '', *paragraphs])) or None
    if not title or not event_date:
        return None
    city = 'Zlín'
    if venue:
        city_match = re.search(r',\s*([^,|]+)$', venue)
        if city_match:
            city = city_match.group(1).strip()
        elif 'Holešov' in venue:
            city = 'Holešov'
        elif 'Luhačovice' in venue:
            city = 'Luhačovice'
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': venue,
        'city': city,
        'country_code': 'CZ',
        'description': description,
        'type': 'concert',
    }


class FilharmonieZlinCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='filharmonie_zlin_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue', 'city', 'country_code', 'description', 'type'],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
    )

    def scrape(self):
        session = requests.Session()
        return [event for url in find_event_links(session) if (event := extract_event(session, url))]


def main():
    FilharmonieZlinCrawler().run()


if __name__ == '__main__':
    main()
