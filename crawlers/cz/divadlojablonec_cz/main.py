import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.divadlojablonec.cz'
PROGRAM_URL = f'{BASE_URL}/program'
SOURCE_URL = f'{BASE_URL}/'
SOURCE = 'Městské divadlo Jablonec nad Nisou'
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


def parse_datetime(text):
    match = re.search(
        r'(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(20\d{2}).*?'
        r'(?:od|v)\s*(\d{1,2})(?:[:.]\s*(\d{2}))?\s*hodin',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, None
    day, month, year, hour, minute = (int(x or 0) for x in match.groups())
    return f'{year:04d}-{month:02d}-{day:02d}', f'{hour:02d}:{minute:02d}'


def find_event_links(session):
    soup = get_soup(session, PROGRAM_URL)
    links = []
    for anchor in soup.select('a[href]'):
        href = urljoin(BASE_URL, anchor['href'])
        if urlparse(href).netloc != urlparse(BASE_URL).netloc or href == PROGRAM_URL:
            continue
        text = clean_text(anchor.get_text(' ', strip=True))
        if re.search(r'\b\d{1,2}\.\s*\d{1,2}\.\s*20\d{2}\b', text):
            links.append(href)
    return list(dict.fromkeys(links))


def extract_description(soup, title):
    content = soup.select_one('.event-description')
    if not content:
        return title or None
    paragraphs = []
    for node in content.select('p'):
        text = clean_text(node.get_text(' ', strip=True))
        if not text or text in {'Program ke stažení', 'Vstupenky'} or len(text) <= 1:
            continue
        paragraphs.append(text)
    return clean_text('\n\n'.join([title] + paragraphs)) or None


def extract_event(session, url):
    soup = get_soup(session, url)
    title_node = soup.select_one('h1.event-title') or soup.select_one('h1')
    subtitle_node = soup.select_one('p.event-subtitle')
    title = clean_text(title_node.get_text(' ', strip=True)) if title_node else None
    date, time_from = parse_datetime(
        subtitle_node.get_text(' ', strip=True) if subtitle_node else ''
    )
    if not title or not date:
        return None
    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': 'Městské divadlo Jablonec nad Nisou',
        'city': 'Jablonec nad Nisou',
        'description': extract_description(soup, title),
        'type': 'concert',
    }


class DivadloJablonecCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='divadlojablonec_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue', 'city', 'description', 'type'],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
    )

    def scrape(self):
        session = requests.Session()
        events = []
        for url in find_event_links(session):
            event = extract_event(session, url)
            if event:
                events.append(event)
        return events


def main():
    DivadloJablonecCrawler().run()


if __name__ == '__main__':
    main()
