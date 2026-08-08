import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.corbridgefestival.co.uk/'
SOURCE = 'Corbridge Chamber Music Festival'
EVENTS_API_URL = urljoin(SOURCE_URL, 'events?format=json')
TIMEZONE = ZoneInfo('Europe/London')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    soup = BeautifulSoup(value, 'html.parser')
    text = soup.get_text('\n', strip=True)
    text = html.unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_timestamp(value):
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=TIMEZONE)
    except (TypeError, ValueError, OSError):
        return None


def parse_location(value):
    if not isinstance(value, dict):
        return None

    venue = clean_text(value.get('addressTitle'))
    address_parts = [
        clean_text(value.get('addressLine1')),
        clean_text(value.get('addressLine2')),
        clean_text(value.get('addressCountry')),
    ]
    combined = ' '.join(part for part in address_parts if part)
    if not venue:
        return None

    # The festival's published events are held in Corbridge. Squarespace's
    # addressCountry is often blank, but addressLine2 carries the town.
    if re.search(r'\bcorbridge\b', combined, flags=re.IGNORECASE):
        return venue, 'Corbridge'
    return None


def parse_event(item):
    if not isinstance(item, dict):
        return None

    title = clean_text(item.get('title'))
    start = parse_timestamp(item.get('startDate'))
    location = parse_location(item.get('location'))
    path = item.get('fullUrl')
    if not title or start is None or not location or not isinstance(path, str) or not path.strip():
        return None

    venue, city = location
    description = clean_text(item.get('body')) or clean_text(item.get('excerpt')) or None

    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': urljoin(SOURCE_URL, path),
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class CorbridgeFestivalCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='corbridgefestival_co_uk',
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
        try:
            response = requests.get(EVENTS_API_URL, headers=HEADERS, timeout=45)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch Corbridge festival events',
                event='crawler_fetch_failed',
                level='error',
                url=EVENTS_API_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        items = []
        for key in ('upcoming', 'past'):
            value = payload.get(key, [])
            if isinstance(value, list):
                items.extend(value)

        records = []
        for item in items:
            record = parse_event(item)
            if record:
                records.append(record)

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'], record['title'], record['url']
            ),
        )


def main():
    CorbridgeFestivalCoUkCrawler().run()


if __name__ == '__main__':
    main()
