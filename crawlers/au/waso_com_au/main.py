import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.waso.com.au/'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on/')
SOURCE = 'West Australian Symphony Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

STATE_PATTERN = re.compile(
    r'(?:,|\n)\s*([^,\n]+?)\s+(?:ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\s+\d{4}\b',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_next_data(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    node = BeautifulSoup(response.text, 'html.parser').select_one('#__NEXT_DATA__')
    if not node or not node.string:
        raise ValueError('Page has no Next.js data payload')
    return json.loads(node.string)


def find_event_data(page):
    grid = page.get('properties', {}).get('contentGrid') or {}
    for item in grid.get('items') or []:
        properties = (item.get('content') or {}).get('properties') or {}
        data = properties.get('ssrWhatsOnEventsData')
        if data:
            return data.get('events') or []
    return []


def event_url(event):
    route = event.get('route') or ''
    if not route:
        return ''
    url = urljoin(SOURCE_URL, route)
    if urlparse(url).netloc.lower() not in {'www.waso.com.au', 'waso.com.au'}:
        return ''
    return url


def city_from_address(address):
    match = STATE_PATTERN.search(clean_text(address))
    return clean_text(match.group(1)) if match else ''


def rich_text_description(properties):
    parts = []
    for value in (properties.get('subtitle'), properties.get('seoMetaDescription')):
        text = clean_text(value)
        if text and text not in parts:
            parts.append(text)

    # Programme and overview prose are top-level rich-text blocks. Restricting
    # the traversal avoids unrelated artist biographies and footer copy.
    for grid_name in ('contentGridAboveSidebar', 'contentGrid', 'contentGridBelowSidebar'):
        grid = properties.get(grid_name) or {}
        for item in grid.get('items') or []:
            content = item.get('content') or {}
            block = content.get('properties') or {}
            if content.get('contentType') != 'richTextEditor':
                continue
            if block.get('componentIsActive') is False:
                continue
            intro = block.get('introContent') or {}
            markup = intro.get('markup') if isinstance(intro, dict) else intro
            text = clean_text(markup)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def records_from_detail(url, page):
    properties = page.get('properties') or {}
    production = properties.get('ssrProductionData') or {}
    title = clean_text(
        properties.get('titleOverride')
        or properties.get('tessituraTitle')
        or production.get('name')
        or page.get('name')
    )
    description = rich_text_description(properties)
    records = []

    for performance in production.get('performances') or []:
        venue_data = performance.get('venue') or {}
        venue = clean_text(venue_data.get('name'))
        city = city_from_address(venue_data.get('address'))
        try:
            starts_at = datetime.fromisoformat(performance.get('date') or '')
            event_date = starts_at.date().isoformat()
            time_from = starts_at.strftime('%H:%M')
        except (TypeError, ValueError):
            continue
        if not title or not venue or not city:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'AU',
            'description': description,
        })
    return records


def scrape_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    listing = get_next_data(session, LISTING_URL)
    page = listing['props']['pageProps']['page']
    records = []

    for event in find_event_data(page):
        url = event_url(event)
        if not url:
            continue
        try:
            detail = get_next_data(session, url)['props']['pageProps']['page']
            records.extend(records_from_detail(url, detail))
        except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as error:
            log_message(
                'Failed to scrape WASO event detail',
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


class WasoComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='waso_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        # The WASO calendar also contains philanthropy and community events;
        # classification prevents those from being uploaded as concerts.
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        return scrape_concerts()


def main():
    WasoComAuCrawler().run()


if __name__ == '__main__':
    main()
