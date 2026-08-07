import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://bristolbeacon.org/'
EVENT_SITEMAP_URL = f'{SOURCE_URL}event-sitemap.xml/'
SOURCE = 'Bristol Beacon'
CITY = 'Bristol'

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
    text = text.replace('\xa0', ' ').replace('\u200b', '').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value):
    text = clean_text(value)
    text = re.sub(r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+', '', text, flags=re.I)
    try:
        return datetime.strptime(text, '%d %b %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)', clean_text(value))
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def event_urls(session):
    response = session.get(EVENT_SITEMAP_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'xml')
    return sorted(
        {
            clean_text(location)
            for location in soup.select('url > loc')
            if '/whats-on/' in clean_text(location)
        }
    )


def event_description(soup):
    parts = []
    for selector in ('.c-event-introduction', '.c-event-important-info'):
        text = clean_text(soup.select_one(selector))
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def records_from_html(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    title = clean_text(soup.select_one('.c-event-masthead__title'))
    venue = clean_text(soup.select_one('.c-event-masthead__meta-label--venue'))
    description = event_description(soup)
    if not title or not venue:
        return []

    instances = soup.select('.c-event-instance')
    if not instances:
        instances = [soup.select_one('.c-event-masthead')]

    records = []
    seen = set()
    for instance in instances:
        if not instance:
            continue
        date_node = instance.select_one('.c-event-instance__date time')
        if not date_node:
            date_node = instance.select_one('.c-event-masthead__meta-label--date')
        event_date = parse_date(date_node)
        time_from = parse_time(instance.select_one('.c-event-instance__time'))
        if not event_date:
            continue
        key = (event_date, time_from)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
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
            }
        )
    return records


def fetch_records(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return records_from_html(url, response.text)


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda item: (item['date'], item['time_from'] or '', item['title'], item['url']),
    )


class BristolBeaconOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bristolbeacon_org',
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
    BristolBeaconOrgCrawler().run()


if __name__ == '__main__':
    main()
