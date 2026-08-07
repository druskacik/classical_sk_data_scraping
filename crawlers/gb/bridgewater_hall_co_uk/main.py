import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.bridgewater-hall.co.uk/'
SOURCE = 'The Bridgewater Hall'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on/')
DEFAULT_VENUE = 'The Bridgewater Hall'
CITY = 'Manchester'

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
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def build_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount(
        'https://',
        HTTPAdapter(
            max_retries=Retry(
                total=2,
                backoff_factor=0.5,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=('GET',),
            )
        ),
    )
    return session


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def event_urls(session):
    first_page = get_soup(session, LISTING_URL)
    pages = [1]
    for link in first_page.select('a[href*="/whats-on/page/"]'):
        match = re.search(r'/whats-on/page/(\d+)/?', link.get('href', ''))
        if match:
            pages.append(int(match.group(1)))

    soups = [first_page]
    if max(pages) > 1:
        with ThreadPoolExecutor(max_workers=8) as executor:
            soups.extend(executor.map(
                lambda page: get_soup(session, urljoin(LISTING_URL, f'page/{page}/')),
                range(2, max(pages) + 1),
            ))

    urls = []
    for soup in soups:
        for link in soup.select('.c-event-item__heading a[href]'):
            url = urljoin(SOURCE_URL, link.get('href'))
            if re.match(r'^https://www\.bridgewater-hall\.co\.uk/whats-on/[^/]+/?$', url):
                urls.append(url)
    return list(dict.fromkeys(urls))


def parse_datetime(value):
    text = clean_text(value)
    match = re.search(
        r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+'
        r'(\d{1,2}\s+[A-Za-z]+\s+\d{4})'
        r'(?:\s+(\d{1,2}(?:[.:]\d{2})?\s*(?:am|pm)))?',
        text,
        re.I,
    )
    if not match:
        return None
    try:
        event_date = datetime.strptime(match.group(1), '%d %B %Y').date().isoformat()
    except ValueError:
        return None

    time_from = None
    if match.group(2):
        normalized = match.group(2).lower().replace('.', ':').replace(' ', '')
        time_match = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?(am|pm)', normalized)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            if 1 <= hour <= 12 and minute <= 59:
                if time_match.group(3) == 'pm' and hour != 12:
                    hour += 12
                elif time_match.group(3) == 'am' and hour == 12:
                    hour = 0
                time_from = f'{hour:02d}:{minute:02d}'
    return event_date, time_from


def event_description(soup):
    parts = []
    subheading = clean_text(soup.select_one('.c-event-masthead .c-event-item__subheading'))
    if subheading:
        parts.append(subheading)
    for row in soup.select('.page-content.c-construkt .c-construkt-row'):
        if 'c-construkt-row--has-tint' in row.get('class', []):
            continue
        text = clean_text(row)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_event(url, content):
    soup = BeautifulSoup(content, 'html.parser')
    title = clean_text(soup.select_one('h1.c-event-item__heading'))
    venue = clean_text(soup.select_one('.c-event__venue')) or DEFAULT_VENUE
    description = event_description(soup)
    if not title or not venue:
        return []

    records = []
    for element in soup.select('.c-event-masthead .c-event-item__datetime'):
        parsed = parse_datetime(element)
        if not parsed:
            continue
        event_date, time_from = parsed
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': CITY,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = build_session()
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(session.get, url, timeout=45): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                response = future.result()
                response.raise_for_status()
                records.extend(parse_event(url, response.content))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Bridgewater Hall event',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda item: (item['date'], item['time_from'] or '', item['title'], item['venue']),
    )


class BridgewaterHallCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bridgewater_hall_co_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
    BridgewaterHallCoUkCrawler().run()


if __name__ == '__main__':
    main()
