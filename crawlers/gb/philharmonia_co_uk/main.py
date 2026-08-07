import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://philharmonia.co.uk/'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on/')
SOURCE = 'Philharmonia Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# The site displays a hall (but not a city) on event pages. These are its
# recurring venues; title prefixes cover the occasional venue-less rehearsal.
VENUE_LOCATIONS = {
    'Bedford Corn Exchange': ('Bedford', 'GB'),
    'Berlin Philharmonie': ('Berlin', 'DE'),
    'Bold Tendencies, Peckham': ('London', 'GB'),
    'Cadogan Hall': ('London', 'GB'),
    'De Montfort Hall': ('Leicester', 'GB'),
    'Marlowe Theatre, Canterbury': ('Canterbury', 'GB'),
    'Mikkeli Music Festival, Finland': ('Mikkeli', 'FI'),
    'Royal Albert Hall': ('London', 'GB'),
    'Royal Concert Hall, Nottingham': ('Nottingham', 'GB'),
    'Royal Festival Hall': ('London', 'GB'),
    'Southbank Centre': ('London', 'GB'),
    'Symphony Hall, Birmingham': ('Birmingham', 'GB'),
    'The Anvil, Basingstoke': ('Basingstoke', 'GB'),
    'The Glasshouse, Gateshead': ('Gateshead', 'GB'),
    'The Haymarket, Basingstoke': ('Basingstoke', 'GB'),
    'Wolkenturm, Grafenegg': ('Grafenegg', 'AT'),
}

TITLE_CITIES = {
    'basingstoke': ('Basingstoke', 'GB'),
    'bedford': ('Bedford', 'GB'),
    'berlin': ('Berlin', 'DE'),
    'birmingham': ('Birmingham', 'GB'),
    'canterbury': ('Canterbury', 'GB'),
    'gateshead': ('Gateshead', 'GB'),
    'leicester': ('Leicester', 'GB'),
    'london': ('London', 'GB'),
    'nottingham': ('Nottingham', 'GB'),
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_items():
    items = []
    seen = set()
    page_url = LISTING_URL

    while page_url:
        soup = get_soup(page_url)
        for card in soup.select('.c-event-card'):
            anchor = card.select_one('.c-event-card__title a[href]')
            if not anchor:
                continue
            url = urljoin(SOURCE_URL, anchor['href'])
            if url in seen:
                continue
            seen.add(url)
            items.append({
                'url': url,
                'title': clean_text(anchor),
                'date_text': clean_text(card.select_one('.c-event-card__date')),
                'venue': clean_text(card.select_one('.c-event-card__venue')),
                'description': clean_text(card.select_one('.c-event-card__description')),
            })

        next_link = soup.select_one('a.next.page-numbers[href]')
        next_url = urljoin(SOURCE_URL, next_link['href']) if next_link else None
        page_url = next_url if next_url and next_url != page_url else None
    return items


def parse_date(value):
    match = re.search(
        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) '
        r'(\d{1,2} [A-Za-z]+ \d{4})',
        value,
    )
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), '%d %B %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', value, re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'pm':
        hour += 12
    return f'{hour:02d}:{match.group(2) or "00"}'


def resolve_location(title, venue):
    if not venue or venue.lower() == 'multiple venues':
        return None
    if venue in VENUE_LOCATIONS:
        return (*VENUE_LOCATIONS[venue], venue)

    text = f'{title} {venue}'.lower()
    for token, (city, country_code) in TITLE_CITIES.items():
        if token in text:
            return city, country_code, venue
    return None


def description_from(soup, fallback):
    # The first content container comprises artists, programme, and the long
    # editorial body. The following "Need to know" container is ticketing data.
    container = soup.select_one('.c-page .c-container:not(.c-container--has-stave)')
    return clean_text(container) or fallback or None


def parse_detail(item):
    soup = get_soup(item['url'])
    title = clean_text(soup.select_one('.c-masthead__title')) or item['title']
    # Cards give the first performance in full even when a detail masthead
    # abbreviates a multi-day range (for example "Wednesday 5 – Saturday 8").
    date_text = item['date_text'] or clean_text(soup.select_one('.c-masthead__datetime'))
    venue = clean_text(soup.select_one('.c-masthead__venue')) or item['venue']
    location = resolve_location(title, venue)
    event_date = parse_date(date_text)
    if not title or not event_date or not location:
        return None
    city, country_code, venue = location
    return {
        'title': title,
        'date': event_date,
        'url': item['url'],
        'time_from': parse_time(date_text),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description_from(soup, item['description']),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    items = listing_items()
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(parse_detail, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                record = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(records, key=lambda row: (row['date'], row['time_from'] or '', row['title']))


class PhilharmoniaCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='philharmonia_co_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
    PhilharmoniaCoUkCrawler().run()


if __name__ == '__main__':
    main()
