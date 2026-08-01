import json
import re
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.mfo.cz'
SOURCE_URL = f'{BASE_URL}/'
SOURCE = 'Moravská filharmonie Olomouc'
PROGRAM_URL = f'{BASE_URL}/program/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; classical-bot/1.0)'}


def clean_text(value):
    value = unescape(value or '').replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def event_json(article):
    script = article.select_one('script[type="application/ld+json"]')
    if not script:
        return {}
    try:
        data = json.loads(script.string or script.get_text())
    except (json.JSONDecodeError, TypeError):
        return {}
    if isinstance(data, list):
        return next((item for item in data if item.get('@type') == 'Event'), {})
    return data if data.get('@type') == 'Event' else {}


def description_from_json(value):
    if not value:
        return None
    soup = BeautifulSoup(unescape(value), 'html.parser')
    return clean_text(soup.get_text('\n', strip=True)) or None


def city_from_location(location):
    name = clean_text((location or {}).get('name'))
    if not name:
        return 'Olomouc'
    if '(' in name:
        return name.split('(', 1)[0].strip()
    if 'Kroměříž' in name:
        return 'Kroměříž'
    if 'Uničov' in name:
        return 'Uničov'
    return 'Olomouc'


def scrape_events(session):
    soup = get_soup(session, PROGRAM_URL)
    records = []
    for article in soup.select('main article.article'):
        link = article.select_one('a[href*="/program/"]')
        if not link:
            continue
        data = event_json(article)
        url = urljoin(BASE_URL, data.get('url') or link.get('href'))
        start = data.get('startDate', '')
        match = re.match(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})', start)
        if not match:
            continue
        location = data.get('location') or {}
        records.append({
            'title': clean_text(data.get('name')) or clean_text(article.select_one('.article__title').get_text(' ', strip=True)),
            'date': match.group(1),
            'url': url,
            'time_from': match.group(2),
            'time_to': None,
            'venue': clean_text(location.get('name')) or None,
            'city': city_from_location(location),
            'country_code': 'CZ',
            'description': description_from_json(data.get('description')),
            'type': 'concert',
        })
    return records


class MfoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mfo_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue', 'city', 'description', 'type'],
        dedupe_subset=['title', 'date', 'url', 'time_from'],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
    )

    def scrape(self):
        with requests.Session() as session:
            return scrape_events(session)


def main():
    MfoCrawler().run()


if __name__ == '__main__':
    main()
