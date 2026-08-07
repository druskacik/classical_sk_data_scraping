import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.blackheathhalls.com/'
LISTING_URL = f'{SOURCE_URL}whats-on/'
ARCHIVE_URL = f'{SOURCE_URL}past-events/'
SOURCE = 'Blackheath Halls'
CITY = 'London'
DEFAULT_VENUE = 'Blackheath Halls'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

DATE_TIME_RE = re.compile(
    r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+'
    r'(\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})'
    r'(?:\s*,\s*(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm))?',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response


def listing_urls(session):
    urls = []
    pages = [(LISTING_URL, None)]
    page_number = 1
    while pages:
        url, params = pages.pop(0)
        soup = BeautifulSoup(get_response(session, url, params=params).content, 'html.parser')
        urls.extend(
            urljoin(url, card.get('href'))
            for card in soup.select('.event-card a.button-more-details[href]')
        )

        if url == ARCHIVE_URL or params:
            next_link = soup.find('a', string=re.compile(r'Next page', re.IGNORECASE))
            if next_link:
                page_number += 1
                pages.append((ARCHIVE_URL, {'pagetoshow': page_number}))

        if url == LISTING_URL:
            pages.append((ARCHIVE_URL, {'pagetoshow': 1}))

    return list(dict.fromkeys(urls))


def normalise_time(hour, minute, meridiem):
    if not hour:
        return None
    hour = int(hour)
    minute = int(minute or 0)
    if hour < 1 or hour > 12 or minute > 59:
        return None
    if meridiem.lower() == 'pm' and hour != 12:
        hour += 12
    elif meridiem.lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def parse_performances(soup):
    candidates = []
    time_node = soup.select_one('.event-banner-left .event-detail-time')
    if time_node:
        candidates.append(clean_text(time_node))
    candidates.extend(clean_text(node) for node in soup.select('.book-event-card'))

    performances = []
    for text in candidates:
        for match in DATE_TIME_RE.finditer(text):
            try:
                event_date = datetime.strptime(match.group(1), '%d %b %Y').date().isoformat()
            except ValueError:
                try:
                    event_date = datetime.strptime(match.group(1), '%d %B %Y').date().isoformat()
                except ValueError:
                    continue
            item = (event_date, normalise_time(*match.groups()[1:]))
            if item not in performances:
                performances.append(item)
    return performances


def clean_venue(value):
    venue = clean_text(value)
    # A few partner venues are rendered as "venue, street, London, postcode".
    # Keep the venue name but do not leak postal addresses into this field.
    if re.search(r'\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b', venue, re.IGNORECASE):
        if ',' in venue:
            venue = venue.split(',', 1)[0]
        venue = re.sub(
            r'\s+\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b.*$', '', venue,
            flags=re.IGNORECASE,
        )
    return venue.strip(' ,')


def parse_event(content, url):
    soup = BeautifulSoup(content, 'html.parser')
    title = clean_text(soup.select_one('.event-banner-left h1'))
    venue = clean_venue(soup.select_one('.event-banner-left .event-detail-location'))
    performances = parse_performances(soup)
    if not title or not performances:
        return []

    # All rooms in this venue-specific calendar are at Blackheath Halls. Some
    # older records omit the room, for which the institution is the defensible
    # venue default.
    venue = venue or DEFAULT_VENUE
    description_parts = []
    for article in soup.select('.page-block-content article'):
        text = clean_text(article)
        if text and text not in description_parts:
            description_parts.append(text)
    description = '\n\n'.join(description_parts) or None

    return [
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
        for event_date, time_from in performances
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_event(future.result().content, url))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Blackheath Halls event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class BlackheathHallsComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='blackheathhalls_com',
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
    BlackheathHallsComCrawler().run()


if __name__ == '__main__':
    main()
