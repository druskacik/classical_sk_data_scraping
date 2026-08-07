import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://actorschurch.org/'
SITEMAP_URL = f'{SOURCE_URL}sitemap-0.xml'
SOURCE = "Actors' Church"
VENUE = "St Paul's Church, Covent Garden"
CITY = 'London'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json, application/xml;q=0.9, */*;q=0.8',
}


def rich_text(value):
    if not isinstance(value, dict):
        return ''
    parts = []
    for block in value.get('raw') or []:
        text = block.get('text') if isinstance(block, dict) else None
        if text and text.strip():
            parts.append(text.strip())
    return '\n'.join(parts).strip()


def parse_time(value):
    text = rich_text(value)
    match = re.search(r'(?<!\d)(1[0-2]|0?[1-9])(?:[.:](\d{2}))?\s*([ap])\.?m\.?', text, re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'p':
        hour += 12
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def page_data_url(event_url):
    path = urlparse(event_url).path.strip('/')
    return f'{SOURCE_URL}page-data/{path}/page-data.json'


def event_urls(session):
    response = session.get(SITEMAP_URL, timeout=45)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    urls = []
    for location in root.findall('.//{*}loc'):
        url = (location.text or '').strip()
        path = urlparse(url).path.rstrip('/')
        if path.startswith('/whatson/') and path != '/whatson':
            urls.append(url.rstrip('/') + '/')
    return sorted(set(urls))


def make_record(event_url, payload):
    event = payload.get('result', {}).get('data', {}).get('prismicEvent') or {}
    data = event.get('data') or {}
    title = rich_text(data.get('title'))
    raw_date = data.get('start_date')
    try:
        event_date = date.fromisoformat(raw_date).isoformat()
    except (TypeError, ValueError):
        return None
    if not title:
        return None

    description_parts = [rich_text(data.get('blurb')), rich_text(data.get('body'))]
    description = '\n\n'.join(part for part in description_parts if part) or None
    return {
        'title': title,
        'date': event_date,
        'url': event_url,
        'time_from': parse_time(data.get('time')),
        'venue': VENUE,
        'city': CITY,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def fetch_record(event_url):
    response = requests.get(page_data_url(event_url), headers=HEADERS, timeout=45)
    response.raise_for_status()
    return make_record(event_url, response.json())


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_record, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class ActorsChurchOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='actorschurch_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='potential',
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
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    ActorsChurchOrgCrawler().run()


if __name__ == '__main__':
    main()
