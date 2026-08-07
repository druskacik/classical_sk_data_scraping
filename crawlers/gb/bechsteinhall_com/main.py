import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://bechsteinhall.com/'
SITEMAP_URL = f'{SOURCE_URL}events-sitemap.xml'
SOURCE = 'Bechstein Hall'
VENUE = 'Bechstein Hall'
CITY = 'London'

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


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def event_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    return sorted({
        clean_text(node)
        for node in soup.select('url > loc')
        if '/events/' in clean_text(node)
    })


def calendar_data(soup):
    node = soup.select_one('[data-calendar]')
    if not node:
        return None
    try:
        payload = json.loads(html.unescape(node.get('data-calendar', '')))
    except (json.JSONDecodeError, TypeError):
        return None

    try:
        event_date = date.fromisoformat(payload.get('startDate', '')).isoformat()
    except (TypeError, ValueError):
        return None

    time_from = payload.get('startTime') or None
    if time_from and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', time_from):
        time_from = None
    return event_date, time_from


def description_text(soup):
    parts = []

    subtitle = clean_text(soup.select_one('.head-event-content_in .p-25-40'))
    if subtitle:
        parts.append(subtitle)

    right_column = soup.select_one('.event-row_right-col')
    if right_column:
        for node in right_column.select('.ui_text-block, .ui_alert-block'):
            text = clean_text(node)
            if text and text not in parts:
                parts.append(text)

    return '\n\n'.join(parts) or None


def parse_event(content, url):
    soup = BeautifulSoup(content, 'html.parser')
    title = clean_text(soup.select_one('h1.event-h') or soup.find('h1'))
    performance = calendar_data(soup)
    if not title or not performance:
        return None

    event_date, time_from = performance
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': VENUE,
        'city': CITY,
        'country_code': 'GB',
        'description': description_text(soup),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(get_response, session, url): url
            for url in urls
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_event(future.result().content, url)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Bechstein Hall event detail',
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


class BechsteinHallComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bechsteinhall_com',
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
    BechsteinHallComCrawler().run()


if __name__ == '__main__':
    main()
