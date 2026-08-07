import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.92ny.org/'
LISTING_URL = 'https://bigseason.92ny.org/concerts'
SOURCE = '92NY'
CITY = 'New York'
VENUE = '92NY'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

DATE_RE = re.compile(
    r'(?:(?:MON|TUE|WED|THU|FRI|SAT|SUN),\s*)?'
    r'([A-Z]{3})\s+(\d{1,2})(?:\s*&\s*'
    r'(?:(?:MON|TUE|WED|THU|FRI|SAT|SUN),\s*)?'
    r'([A-Z]{3})?\s*(\d{1,2}))?,\s*(\d{4})',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text(' ', strip=True)
    return re.sub(r'\s+', ' ', text.replace('\xa0', ' ')).strip()


def parse_dates(value):
    match = DATE_RE.search(clean_text(value))
    if not match:
        return []

    month_one, day_one, month_two, day_two, year = match.groups()
    values = [(month_one, day_one)]
    if day_two:
        values.append((month_two or month_one, day_two))

    parsed = []
    for month, day in values:
        try:
            parsed.append(
                datetime.strptime(f'{month} {day} {year}', '%b %d %Y').date().isoformat()
            )
        except ValueError:
            continue
    return parsed


def card_record_data(card):
    date_node = card.find(string=lambda value: value and DATE_RE.search(value))
    title_node = card.find(['h2', 'h3', 'h4'])
    link = card.find('a', href=True)
    if not date_node or not title_node or not link:
        return None

    title = clean_text(title_node)
    dates = parse_dates(date_node)
    url = urljoin(LISTING_URL, link.get('href'))
    if not title or not dates or not url.startswith(('http://', 'https://')):
        return None

    description_parts = []
    for node in card.find_all('p'):
        text = clean_text(node)
        if text and text != clean_text(date_node) and text.lower() not in {
            'discover more',
            'get tickets',
        } and text not in description_parts:
            description_parts.append(text)

    return title, dates, url, '\n\n'.join(description_parts) or None


def scrape_concerts(session=None):
    session = session or requests.Session()
    session.headers.update(HEADERS)
    response = session.get(LISTING_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    records = []
    for card in soup.select('.news-grid-box'):
        data = card_record_data(card)
        if not data:
            continue
        title, dates, url, description = data
        for event_date in dates:
            records.append({
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': None,
                'venue': VENUE,
                'city': CITY,
                'country_code': 'US',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })

    if not records:
        log_message(
            'No concert cards found',
            event='crawler_empty_listing',
            level='warning',
            url=LISTING_URL,
            record_count=0,
        )

    return sorted(records, key=lambda item: (item['date'], item['title'], item['url']))


class NinetyTwoNyOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='92ny_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
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
        return scrape_concerts()


def main():
    NinetyTwoNyOrgCrawler().run()


if __name__ == '__main__':
    main()
