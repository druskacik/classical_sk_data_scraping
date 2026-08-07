import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://proarte-frankfurt.de/'
API_URL = f'{SOURCE_URL}wp-json/wp/v2/nb_concerts'
SOURCE = 'Pro Arte Frankfurt'
VENUE = 'Alte Oper Frankfurt'
CITY = 'Frankfurt am Main'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = html.unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response


def listing_events(session):
    """Use the public WordPress API to discover current and archived concerts."""
    page = 1
    events = []
    while True:
        response = get_response(
            session,
            API_URL,
            params={'per_page': 100, 'page': page, 'orderby': 'id', 'order': 'desc'},
        )
        payload = response.json()
        events.extend(payload)
        total_pages = int(response.headers.get('X-WP-TotalPages', '1'))
        if page >= total_pages:
            break
        page += 1
    return events


def parse_datetime(soup):
    node = soup.select_one('.calendar-hero-content .teaser-date')
    value = clean_text(node.get_text(' ', strip=True) if node else '')
    match = re.search(
        r'(\d{1,2})\.(\d{1,2})\.(\d{4})\s*,\s*'
        r'(\d{1,2})(?:[.:](\d{2}))?\s*Uhr',
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    try:
        concert_date = date(
            int(match.group(3)), int(match.group(2)), int(match.group(1))
        ).isoformat()
    except ValueError:
        return None, None
    concert_time = f'{int(match.group(4)):02d}:{match.group(5) or "00"}'
    return concert_date, concert_time


def detail_description(soup):
    parts = []
    title = soup.select_one('h1.concert-title')
    narrative = title.find_parent(class_='col-custom-1') if title else None
    if narrative:
        title.extract()
        text = clean_text(narrative.get_text('\n', strip=True))
        if text:
            parts.append(text)

    # The programme is a separate column. Other columns in the same component
    # contain ticket-service information, so select only the one headed Programm.
    for column in soup.select('.ce-twocol .col-custom-2'):
        heading = column.find(['h2', 'h3', 'h4', 'h5', 'h6'])
        if heading and clean_text(heading.get_text()).lower() == 'programm':
            programme = clean_text(column.get_text('\n', strip=True))
            if programme:
                parts.append(programme)
            break
    return '\n\n'.join(parts) or None


def parse_event(event, response_text):
    soup = BeautifulSoup(response_text, 'html.parser')
    title_node = soup.select_one('h1.concert-title')
    title = clean_text(title_node.get_text(' ', strip=True) if title_node else '')
    concert_date, concert_time = parse_datetime(soup)
    url = event.get('link') or ''
    if not title or not concert_date or not url:
        return None
    return {
        'title': title,
        'date': concert_date,
        'url': url,
        'time_from': concert_time,
        'venue': VENUE,
        'city': CITY,
        'country_code': 'DE',
        'description': detail_description(soup),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = listing_events(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(get_response, session, event['link']): event
            for event in events
            if event.get('link')
        }
        for future in as_completed(futures):
            event = futures[future]
            try:
                record = parse_event(event, future.result().text)
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=event.get('link'),
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


class ProarteFrankfurtDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='proarte_frankfurt_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
    ProarteFrankfurtDeCrawler().run()


if __name__ == '__main__':
    main()
