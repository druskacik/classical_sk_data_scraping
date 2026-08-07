import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.stgeorgesbristol.co.uk/'
EVENTS_URL = urljoin(SOURCE_URL, 'whats-on/')
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')
SOURCE = "St George's Bristol"

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    # The site's JSON-LD currently emits apostrophes with a literal backslash.
    text = text.replace("\\'", "'")
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def sitemap_event_urls(session):
    """Use the site's event sitemap when its intermittently published feed is live."""
    try:
        index = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
        sitemap_urls = [
            clean_text(node)
            for node in index.select('sitemap > loc')
            if 'event' in clean_text(node).lower()
        ]
        urls = []
        for sitemap_url in sitemap_urls:
            sitemap = BeautifulSoup(get_response(session, sitemap_url).content, 'xml')
            urls.extend(clean_text(node) for node in sitemap.select('url > loc'))
        return [url for url in urls if '/whats-on/' in url]
    except requests.RequestException as error:
        log_message(
            'Event sitemap unavailable; using the public catalogue',
            event='crawler_discovery_fallback',
            level='warning',
            url=SITEMAP_URL,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        return []


def catalogue_event_urls(session):
    urls = []
    page = 1
    while page <= 100:
        url = EVENTS_URL if page == 1 else urljoin(EVENTS_URL, f'page/{page}/')
        soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
        cards = soup.select('.c-col-card--event')
        if not cards:
            break
        for card in cards:
            link = card.select_one('a.c-col-card__link[href]')
            if link:
                urls.append(urljoin(SOURCE_URL, link['href']))
        next_link = soup.select_one('a.next, a[rel="next"]')
        if not next_link:
            break
        page += 1
    return urls


def event_urls(session):
    # The sitemap can retain past event pages; the catalogue is the dependable
    # source of all currently advertised events.
    urls = sitemap_event_urls(session)
    urls.extend(catalogue_event_urls(session))
    return list(dict.fromkeys(urls))


def event_schema(soup):
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.string or node.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get('@type') == 'Event':
                return item
    return None


def parse_start(value):
    if not value:
        return None, None
    try:
        start = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None, None
    return start.date().isoformat(), start.strftime('%H:%M')


def parse_event(content, fallback_url):
    soup = BeautifulSoup(content, 'html.parser')
    schema = event_schema(soup)
    if not schema:
        return None

    title = clean_text(schema.get('name'))
    event_date, time_from = parse_start(schema.get('startDate'))
    location = schema.get('location') or {}
    address = location.get('address') or {}
    venue = clean_text(location.get('name'))
    city = clean_text(address.get('addressLocality'))
    country_code = clean_text(address.get('addressCountry')).upper()
    url = urljoin(SOURCE_URL, schema.get('url') or fallback_url)
    if not all((title, event_date, url, venue, city, country_code)):
        return None

    description_node = soup.select_one('.c-event-page__intro')
    description = clean_text(description_node) or clean_text(schema.get('description')) or None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_event(future.result().content, url)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape St George\'s Bristol event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class StGeorgesBristolCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='stgeorgesbristol_co_uk',
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
    StGeorgesBristolCrawler().run()


if __name__ == '__main__':
    main()
