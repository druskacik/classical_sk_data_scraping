import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.amadeusorchestra.co.uk/'
CONCERTS_URL = urljoin(SOURCE_URL, 'concerts')
SOURCE = 'Amadeus Orchestra'

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
    text = text.replace('\xa0', ' ').replace('\u200b', '').replace('\u200d', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value):
    try:
        return datetime.strptime(clean_text(value), '%B %d, %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', clean_text(value))
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def parse_location(value):
    text = clean_text(value)
    if ',' not in text:
        return None, None
    venue, city = (part.strip(' ,') for part in text.rsplit(',', 1))
    if not venue or not city:
        return None, None
    return venue, city


def description_from(item):
    parts = []
    for selector in ('.all-pieces', '.all-artists'):
        text = clean_text(item.select_one(selector))
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def record_from_item(item):
    title = clean_text(item.select_one('.all-venue'))
    event_date = parse_date(item.select_one('.all-date'))
    venue, city = parse_location(item.select_one('.all-with'))
    link = item.select_one('a[href]')
    url = urljoin(CONCERTS_URL, link.get('href', '').strip()) if link else CONCERTS_URL

    if not title or not event_date or not venue or not city or not url:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': parse_time(item.select_one('.all-time')),
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': description_from(item),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    response = requests.get(CONCERTS_URL, headers=HEADERS, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    records = []
    seen = set()

    for item in soup.select('.w-dyn-item:has(.all-venue)'):
        record = record_from_item(item)
        if not record:
            log_message(
                'Skipping concert with incomplete required fields',
                event='crawler_item_skipped',
                level='warning',
                url=CONCERTS_URL,
            )
            continue
        key = (record['title'], record['date'], record['time_from'], record['venue'])
        if key not in seen:
            seen.add(key)
            records.append(record)

    return sorted(records, key=lambda record: (record['date'], record['time_from'] or '', record['title']))


class AmadeusOrchestraCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='amadeusorchestra_co_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='classical',
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
    AmadeusOrchestraCoUkCrawler().run()


if __name__ == '__main__':
    main()
