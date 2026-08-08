import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.ukaria.com/'
SOURCE = 'UKARIA Cultural Centre'
EVENTS_URL = urljoin(SOURCE_URL, 'events')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}


def clean_text(value):
    if value is None:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def discover_event_urls(html, base_url=EVENTS_URL):
    soup = BeautifulSoup(html, 'html.parser')
    urls = []
    for link in soup.select('.events_listing a[href]'):
        url = urljoin(base_url, link.get('href'))
        path = urlparse(url).path.rstrip('/')
        if path.startswith('/events/'):
            urls.append(url)
    return list(dict.fromkeys(urls))


def event_jsonld(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or '')
        except (TypeError, json.JSONDecodeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get('@graph'), list):
            candidates.extend(data['@graph'])
        for candidate in candidates:
            event_type = candidate.get('@type') if isinstance(candidate, dict) else None
            if event_type == 'Event' or (
                isinstance(event_type, list) and 'Event' in event_type
            ):
                return candidate
    return None


def iso_datetime(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def description_for(soup):
    writeup = soup.select_one('.event_writeup')
    return clean_text(writeup) or None


def parse_event_page(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    data = event_jsonld(soup)
    if not data:
        return []

    title = clean_text(data.get('name')) or clean_text(soup.select_one('.event_heading h1'))
    location = data.get('location') if isinstance(data.get('location'), dict) else {}
    address = location.get('address') if isinstance(location.get('address'), dict) else {}
    venue = clean_text(location.get('name'))
    city = clean_text(address.get('addressLocality'))
    country_code = clean_text(address.get('addressCountry')).upper()
    if not country_code and venue == 'UKARIA Cultural Centre':
        country_code = 'AU'
    if not city and venue == 'UKARIA Cultural Centre':
        city = 'Mount Barker Summit'

    starts = data.get('startDate') or []
    if isinstance(starts, str):
        starts = [starts]
    if not title or not venue or not city or not country_code:
        return []

    description = description_for(soup)
    records = []
    for value in starts:
        start = iso_datetime(value)
        if not start:
            continue
        records.append({
            'title': title,
            'date': start.date().isoformat(),
            'url': url,
            'time_from': start.strftime('%H:%M'),
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': description,
        })
    return records


class UkariaComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ukaria_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        listing = fetch(session, EVENTS_URL)
        urls = discover_event_urls(listing.text, listing.url)
        records = []

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch, session, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    response = future.result()
                    records.extend(parse_event_page(response.text, response.url))
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch UKARIA event',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )

        return sorted(
            records,
            key=lambda item: (
                item['date'], item['time_from'] or '', item['title'], item['venue']
            ),
        )


def main():
    UkariaComCrawler().run()


if __name__ == '__main__':
    main()
