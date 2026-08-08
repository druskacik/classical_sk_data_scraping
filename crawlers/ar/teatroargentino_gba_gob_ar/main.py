import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://teatroargentino.gba.gob.ar/'
LISTING_URL = urljoin(SOURCE_URL, 'meets/season')
SOURCE = 'Teatro Argentino'
CITY = 'La Plata'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'es-AR,es;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_items(session):
    soup = fetch_soup(session, LISTING_URL)
    items = []
    seen = set()
    for card in soup.select('.card'):
        link = card.select_one('a[href^="/meet/"]')
        title = clean_text(card.select_one('.card-title'))
        date_node = card.select_one('small.text-muted')
        location_nodes = card.select('.card-text')
        venue = clean_text(location_nodes[-1]) if len(location_nodes) >= 2 else ''
        url = urljoin(SOURCE_URL, link.get('href', '')) if link else ''
        date_text = clean_text(date_node)
        if not title or not url or url in seen:
            continue
        match = re.search(r'(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})', date_text)
        if not match:
            continue
        try:
            event_date = datetime.strptime(match.group(1), '%d/%m/%Y').date().isoformat()
        except ValueError:
            continue
        seen.add(url)
        items.append({
            'title': title,
            'date': event_date,
            'time_from': match.group(2),
            'venue': venue,
            'url': url,
        })
    return items


def detail_record(session, item):
    soup = fetch_soup(session, item['url'])
    content = soup.select_one('.meet-content')
    if not content:
        return None

    title = clean_text(content.select_one('.meet-title')) or item['title']
    venue = clean_text(content.select_one('.hall-title')) or item['venue']
    description = clean_text(content.select_one('.meet-description')) or None

    # Cancelled performances remain visible in the archive but are not concerts
    # that took place and should not be uploaded as valid event records.
    if 'suspendida' in title.lower() or 'cancelada' in title.lower():
        return None
    if not title or not venue:
        return None

    return {
        'title': title,
        'date': item['date'],
        'url': item['url'],
        'time_from': item['time_from'],
        'venue': venue,
        'city': CITY,
        'country_code': 'AR',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class TeatroArgentinoGbaGobArCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='teatroargentino_gba_gob_ar',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AR',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            items = listing_items(session)
        except requests.RequestException as error:
            log_message(
                'Failed to fetch Teatro Argentino calendar',
                event='crawler_fetch_failed',
                level='error',
                url=LISTING_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        records = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(detail_record, session, item): item for item in items
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    record = future.result()
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch Teatro Argentino event detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=item['url'],
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


def main():
    TeatroArgentinoGbaGobArCrawler().run()


if __name__ == '__main__':
    main()
