import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.konserthuset.se/'
CALENDAR_URL = urljoin(SOURCE_URL, 'program-och-biljetter/kalender/')
LOAD_MORE_URL = urljoin(SOURCE_URL, 'CalendarSlideBlock/LoadMore/')
SOURCE = 'Konserthuset Stockholm'
CONTENT_GUID = '7734c4c5-5c58-4872-a98b-6b5501531aca'
ARCHIVE_START = '2018-01-01 00:00:00'
PAGE_SIZE = 20

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Referer': CALENDAR_URL,
    'X-Requested-With': 'XMLHttpRequest',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def calendar_payload(skip):
    # Key 1 is "Konsert". Value 1337 is the site's sentinel for all genres.
    return {
        'date': ARCHIVE_START,
        'skip': skip,
        'take': PAGE_SIZE,
        'typefilters[0][Key]': 1,
        'typefilters[0][Value][0]': 1337,
        'lang': 'sv',
        'viewType': 'normal',
        'currentBlockId': 0,
        'contentGuid': CONTENT_GUID,
    }


def event_description(item):
    full_view = item.select_one('.full-view')
    if not full_view:
        return clean_text(item.select_one('[itemprop="description"]')) or None

    parts = []
    ingress = full_view.select_one('.ingress')
    if ingress:
        parts.append(clean_text(ingress))

    # Editorial body and programme lists live in the main (left) column. Avoid
    # the ticket and venue controls in the right column.
    main_column = full_view.select_one('.medium-8.small-12.columns')
    if main_column:
        for node in main_column.select('.tightUpRowlength > p, .listing-header'):
            text = clean_text(node)
            if text and text not in parts:
                parts.append(text)
    return clean_text('\n\n'.join(parts)) or None


def make_record(item):
    link = item.select_one('.js-arrangement-display-initial h3 a[href]')
    start = item.get('data-fulltime', '')
    try:
        start_at = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None

    location = item.select_one('.full-view dd[itemprop="location"]')
    venue_node = location.select_one('[itemprop="name"]') if location else None
    region = location.select_one('[itemprop="addressRegion"]') if location else None
    locality = location.select_one('[itemprop="addressLocality"]') if location else None
    venue = clean_text(venue_node)
    city = clean_text(region.get('content') if region else '')

    # Konserthuset's halls identify the building rather than the municipality
    # in addressLocality; those are safely in Stockholm. Touring locations must
    # provide their own region and are never assigned this default.
    locality_text = clean_text(locality.get('content') if locality else '')
    if not city and 'stockholms konserthus' in locality_text.lower():
        city = 'Stockholm'

    title = clean_text(link)
    url = urljoin(SOURCE_URL, link.get('href')) if link else ''
    if not title or not url or not venue or not city:
        return None

    return {
        'title': title,
        'date': start_at.date().isoformat(),
        'url': url,
        'time_from': start_at.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'SE',
        'description': event_description(item),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    skip = 0

    while True:
        response = session.post(LOAD_MORE_URL, data=calendar_payload(skip), timeout=90)
        response.raise_for_status()
        payload = response.json()
        soup = BeautifulSoup(payload.get('html') or '', 'html.parser')
        items = soup.select('li[id^="page-"][data-fulltime]')
        if not items:
            break

        for item in items:
            record = make_record(item)
            if record:
                records.append(record)

        skip += len(items)
        log_message(
            'Calendar page scraped',
            event='crawler_page_scraped',
            url=LOAD_MORE_URL,
            record_count=len(records),
        )
        if payload.get('hideSelf') or len(items) < PAGE_SIZE:
            break

    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'], record['title'], record['url']),
    )


class KonserthusetSeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='konserthuset_se',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='SE',
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
    KonserthusetSeCrawler().run()


if __name__ == '__main__':
    main()
