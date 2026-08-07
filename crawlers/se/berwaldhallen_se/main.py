import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.berwaldhallen.se/'
SOURCE = 'Berwaldhallen'
CALENDAR_URL = urljoin(SOURCE_URL, 'kalendarium')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        'Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'sv-SE,sv;q=0.9,en;q=0.8',
}
COUNTRY_CODES = {
    'sverige': 'SE',
    'sweden': 'SE',
    'danmark': 'DK',
    'denmark': 'DK',
    'norge': 'NO',
    'norway': 'NO',
    'finland': 'FI',
    'tyskland': 'DE',
    'germany': 'DE',
    'frankrike': 'FR',
    'france': 'FR',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def canonical_url(value):
    parts = urlsplit(urljoin(SOURCE_URL, value))
    path = parts.path.rstrip('/')
    return urlunsplit(('https', 'www.berwaldhallen.se', path, '', ''))


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def discover_urls(session):
    response = session.get(CALENDAR_URL, timeout=45)
    response.raise_for_status()
    text = response.text

    paths = set(re.findall(r'href=["\'](/konsert/[^"\'#?]+)', text))
    # Next.js server data JSON-escapes URLs that are not rendered as anchors.
    paths.update(re.findall(r'\\"url\\":\\"(/konsert/[^\\"?#]+)', text))
    return {canonical_url(path) for path in paths}


def json_ld_objects(soup):
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(node.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict):
                yield item


def event_objects(data):
    event_type = data.get('@type')
    types = event_type if isinstance(event_type, list) else [event_type]
    if 'Event' in types:
        yield data
    children = data.get('subEvent') or []
    if isinstance(children, dict):
        children = [children]
    for child in children:
        if isinstance(child, dict):
            yield child


def country_code(address):
    value = clean_text(address.get('addressCountry'))
    if len(value) == 2 and value.isalpha():
        return value.upper()
    return COUNTRY_CODES.get(value.casefold())


def detail_records(session, url):
    soup = get_soup(session, url)
    page_title = clean_text(soup.select_one('h1'))
    records = []
    seen = set()

    for data in json_ld_objects(soup):
        series_description = clean_text(data.get('description')) or None
        for event in event_objects(data):
            location = event.get('location') or {}
            address = location.get('address') or {}
            venue = clean_text(location.get('name'))
            city = clean_text(address.get('addressLocality'))
            code = country_code(address)
            title = clean_text(event.get('name')) or page_title
            start_value = event.get('startDate')
            if not all((title, start_value, venue, city, code)):
                continue
            try:
                start = datetime.fromisoformat(start_value.replace('Z', '+00:00'))
            except (TypeError, ValueError):
                continue

            description = clean_text(event.get('description')) or series_description
            key = (start.date().isoformat(), start.strftime('%H:%M'), venue, title)
            if key in seen:
                continue
            seen.add(key)
            records.append({
                'title': title,
                'date': start.date().isoformat(),
                'url': url,
                'time_from': start.strftime('%H:%M'),
                'venue': venue,
                'city': city,
                'country_code': code,
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


class BerwaldhallenSeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='berwaldhallen_se',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='SE',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        urls = discover_urls(session)
        records = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(detail_records, session, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    records.extend(future.result())
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape Berwaldhallen concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(records, key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['venue']
        ))


def main():
    BerwaldhallenSeCrawler().run()


if __name__ == '__main__':
    main()
