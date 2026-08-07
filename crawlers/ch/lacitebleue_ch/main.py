import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://lacitebleue.ch/fr'
EVENTS_URL = f'{SOURCE_URL}/evenements'
SITEMAP_URL = 'https://lacitebleue.ch/sitemap.xml'
SOURCE = 'La Cité Bleue'
DEFAULT_VENUE = 'La Cité Bleue'
DEFAULT_CITY = 'Genève'

LOCATION_MAP = {
    # The site identifies this off-site production explicitly; Concorde's
    # published address is in the Geneva municipality of Vernier.
    'Espace Concorde': ('Espace Concorde', 'Vernier'),
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-CH,fr;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response


def sitemap_xml(response):
    """Decode the site's XML, which is currently wrapped as a JSON Buffer."""
    try:
        payload = response.json()
    except (requests.JSONDecodeError, ValueError):
        return response.text
    if payload.get('type') == 'Buffer' and isinstance(payload.get('data'), list):
        return bytes(payload['data']).decode('utf-8')
    raise ValueError('Unexpected sitemap response')


def event_urls(session):
    xml = sitemap_xml(get_response(session, SITEMAP_URL))
    soup = BeautifulSoup(xml, 'xml')
    pattern = re.compile(r'^https://lacitebleue\.ch/fr/evenements/\d{4}-\d{4}/[^/]+$')
    return list(
        dict.fromkeys(
            clean_text(node)
            for node in soup.select('loc')
            if pattern.fullmatch(clean_text(node))
        )
    )


def performance_year(url, month):
    match = re.search(r'/evenements/(\d{4})-(\d{4})/', url)
    if not match:
        return None
    # Seasons begin in autumn and end the following summer.
    return int(match.group(1) if month >= 8 else match.group(2))


def performance_records(soup, url):
    records = []
    for item in soup.select('.event-id-card__performance-item'):
        text = clean_text(item)
        match = re.search(
            r'\b(\d{1,2})\.(\d{1,2})\b[\s\S]*?\b(\d{1,2}):(\d{2})\b',
            text,
        )
        if not match:
            continue
        day, month, hour, minute = map(int, match.groups())
        year = performance_year(url, month)
        try:
            event_date = date(year, month, day).isoformat()
        except (TypeError, ValueError):
            continue
        if hour > 23 or minute > 59:
            continue
        records.append((event_date, f'{hour:02d}:{minute:02d}'))
    return list(dict.fromkeys(records))


def make_records(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    title_node = soup.select_one('.staff-header-fallback-title')
    title = clean_text(title_node)
    if not title:
        page_title = clean_text(soup.title)
        title = re.sub(r'\s+[–-]\s+La Cité Bleue$', '', page_title).strip()

    performances = performance_records(soup, url)
    if not title or not performances:
        return []

    location_icon = soup.find(
        'span', class_=lambda value: value and 'i-ri:map-pin-2-line' in value
    )
    location = clean_text(location_icon.parent) if location_icon else ''
    if location:
        venue_city = LOCATION_MAP.get(location)
        if not venue_city:
            return []
        venue, city = venue_city
    else:
        venue, city = DEFAULT_VENUE, DEFAULT_CITY

    content = soup.select_one('.event-exhibition-page-content .contentBlocks')
    description = clean_text(content) or None
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'CH',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date, time_from in performances
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(make_records(url, future.result().text))
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape La Cité Bleue event',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    log_message(
        'La Cité Bleue archive parsed',
        event='crawler_scrape_completed',
        url=EVENTS_URL,
        record_count=len(records),
    )
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class LaCiteBleueChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='lacitebleue_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
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
    LaCiteBleueChCrawler().run()


if __name__ == '__main__':
    main()
