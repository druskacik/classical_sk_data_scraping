import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.sferisterio.it/'
SOURCE = 'Macerata Opera Festival – Sferisterio'
SITEMAP_URL = urljoin(SOURCE_URL, 'wp-sitemap.xml')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'it-IT,it;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response


def event_sitemaps(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    urls = [clean_text(node) for node in soup.select('sitemap > loc')]
    return [
        url for url in urls
        if re.search(r'/post-(?:mof|off|pop)-sitemap\d*\.xml$', url)
    ]


def event_urls(session):
    urls = set()
    for sitemap in event_sitemaps(session):
        soup = BeautifulSoup(get_response(session, sitemap).content, 'xml')
        for node in soup.select('url > loc'):
            url = clean_text(node)
            if url.startswith(SOURCE_URL) and '/wp-content/' not in url:
                urls.add(url)
    return urls


def json_objects(value):
    """Decode the site's JSON-LD, which is sometimes a comma-separated list."""
    # Its event template emits a trailing comma before every closing object and
    # several top-level objects without surrounding array brackets.
    repaired = re.sub(r',\s*([}\]])', r'\1', value).strip().strip(',')
    try:
        payload = json.loads(f'[{repaired}]')
    except json.JSONDecodeError:
        payload = None
    if payload is not None:
        yield from payload
        return

    decoder = json.JSONDecoder()
    position = 0
    while position < len(value):
        match = re.search(r'[\[{]', value[position:])
        if not match:
            return
        position += match.start()
        try:
            payload, position = decoder.raw_decode(value, position)
        except json.JSONDecodeError:
            position += 1
            continue
        if isinstance(payload, list):
            yield from payload
        else:
            yield payload


def theater_events(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        for payload in json_objects(script.string or script.get_text()):
            if isinstance(payload, dict) and payload.get('@type') in (
                'TheaterEvent', 'MusicEvent', 'Event'
            ):
                yield payload
            elif isinstance(payload, dict) and isinstance(payload.get('@graph'), list):
                for item in payload['@graph']:
                    if isinstance(item, dict) and item.get('@type') in (
                        'TheaterEvent', 'MusicEvent', 'Event'
                    ):
                        yield item


def location_value(location, key):
    if not isinstance(location, dict):
        return ''
    return clean_text(location.get(key))


def resolve_location(event):
    location = event.get('location') or {}
    venue = location_value(location, 'name')
    address = location.get('address') if isinstance(location, dict) else ''
    if isinstance(address, dict):
        city = clean_text(address.get('addressLocality'))
        address_text = clean_text(' '.join(str(value) for value in address.values()))
    else:
        city = ''
        address_text = clean_text(address)

    location_text = f'{venue} {address_text}'.casefold()
    if not city and any(term in location_text for term in (
        'sferisterio', 'lauro rossi', 'macerata',
    )):
        city = 'Macerata'
    if not venue or not city:
        return None, None
    return venue, city


def page_description(soup):
    main = soup.find('main')
    if not main:
        return None
    for unwanted in main.select('script, style, nav, .thedata-opera, .tocart'):
        unwanted.decompose()
    return clean_text(main) or None


def parse_start(value):
    match = re.match(r'^(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2}))?', value or '')
    if not match:
        return None
    try:
        event_date = datetime.strptime(match.group(1), '%Y-%m-%d').date().isoformat()
    except ValueError:
        return None
    time_from = f'{match.group(2)}:{match.group(3)}' if match.group(2) else None
    return event_date, time_from


def detail_records(session, url):
    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
    description = page_description(soup)
    records = []
    for event in theater_events(soup):
        title = clean_text(event.get('name'))
        start = parse_start(event.get('startDate'))
        venue, city = resolve_location(event)
        if not title or not start or not venue or not city:
            continue
        records.append({
            'title': title,
            'date': start[0],
            'url': url,
            'time_from': start[1],
            'venue': venue,
            'city': city,
            'country_code': 'IT',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(detail_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape Sferisterio event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    unique = {
        (record['title'], record['date'], record['time_from'], record['venue']): record
        for record in records
    }
    return sorted(
        unique.values(),
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class SferisterioItCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='sferisterio_it',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='IT',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    SferisterioItCrawler().run()


if __name__ == '__main__':
    main()
