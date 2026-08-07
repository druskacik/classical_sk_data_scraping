import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.konzerte-basel.ch/home'
SOURCE = 'Allgemeine Musikgesellschaft Basel'
CITY = 'Basel'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_events(session):
    soup = get_soup(session, SOURCE_URL)
    events = {}
    for link in soup.select('a.teeser[href*="/event/"]'):
        url = urljoin(SOURCE_URL, link.get('href', ''))
        if not url or url in events:
            continue
        text_link = link if link.select_one('.teaser-el-1') else None
        if text_link is None:
            text_link = soup.select_one(f'a.teeser[href="{link.get("href")}"] .teaser-el-1')
            text_link = text_link.find_parent('a') if text_link else None
        if not text_link:
            continue
        interpreters = [clean_text(node) for node in text_link.select('.interpret')]
        interpreters = [value for value in interpreters if value]
        venue_node = text_link.select_one('.teaser-el-2')
        events[url] = {
            'url': url,
            'listing_title': interpreters[0] if interpreters else '',
            'listing_venue': clean_text(venue_node),
        }
    return list(events.values())


def parse_detail(soup, event):
    body = soup.select_one('.detail-body-text-content')
    header = body.select_one('.child1') if body else None
    date_node = header.select_one('strong') if header else None
    header_text = clean_text(header)
    date_match = re.search(r'\b(\d{2}\.\d{2}\.\d{4})\b', header_text)
    time_match = re.search(r'\b([01]\d|2[0-3]):[0-5]\d\b', header_text)
    if not date_match and date_node:
        date_match = re.search(r'\b(\d{2}\.\d{2}\.\d{4})\b', clean_text(date_node))

    interpreters = [clean_text(node) for node in body.select('p.interpret')] if body else []
    interpreters = [value for value in interpreters if value]
    # Listing titles omit role labels ("Klavier", "Leitung", etc.) that are
    # nested inside the first performer paragraph on detail pages.
    title = event['listing_title'] or (interpreters[0] if interpreters else '')
    title = re.sub(r'\s+', ' ', title).strip()
    venue = event['listing_venue']
    if not title or not date_match or not venue:
        return None
    try:
        event_date = datetime.strptime(date_match.group(1), '%d.%m.%Y').date().isoformat()
    except ValueError:
        return None

    description_nodes = body.select('p.interpret, p.werke') if body else []
    description_parts = [clean_text(node) for node in description_nodes]
    description_parts = [value for value in description_parts if value]
    return {
        'title': title,
        'date': event_date,
        'url': event['url'],
        'time_from': time_match.group(0) if time_match else None,
        'venue': venue,
        'city': CITY,
        'country_code': 'CH',
        'description': '\n\n'.join(description_parts) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = listing_events(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_soup, session, event['url']): event for event in events}
        for future in as_completed(futures):
            event = futures[future]
            try:
                record = parse_detail(future.result(), event)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=event['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class KonzerteBaselChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='konzerte_basel_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    KonzerteBaselChCrawler().run()


if __name__ == '__main__':
    main()
