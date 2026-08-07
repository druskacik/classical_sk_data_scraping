import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://theglasshouseicm.org/'
PROGRAM_URL = urljoin(SOURCE_URL, 'whats-on/')
SOURCE = 'The Glasshouse International Centre for Music'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

HOME_VENUE_TERMS = (
    'the glasshouse',
    'sage one',
    'sage two',
    'foyle music centre',
    'northern rock foundation hall',
    'concourse',
    'brasserie',
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_items(session):
    """Read every page exposed by the public What's On pagination."""
    url = PROGRAM_URL
    seen_pages = set()
    items = {}
    while url and url not in seen_pages:
        seen_pages.add(url)
        soup = get_soup(session, url)
        for card in soup.select('.c-col-card--event'):
            link = card.select_one('a.c-col-card__link[href]')
            if not link:
                continue
            event_url = urljoin(url, link['href'])
            if urlparse(event_url).netloc != urlparse(SOURCE_URL).netloc:
                continue
            items[event_url] = {
                'url': event_url,
                'title': clean_text(card.select_one('.c-col-card__title')),
                'date_text': clean_text(card.select_one('.c-col-card__date')),
                'venue': clean_text(card.select_one('.c-col-card__venue')),
            }
        next_link = soup.select_one('.event-pagination a.next[href], a.next.page-numbers[href]')
        url = urljoin(url, next_link['href']) if next_link else None
    return list(items.values())


def parse_date(value):
    value = clean_text(value)
    value = re.sub(r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+', '', value)
    value = re.sub(r'(\d)(st|nd|rd|th)\b', r'\1', value, flags=re.I)
    try:
        parsed = datetime.strptime(value, '%d %B %Y').date()
    except ValueError:
        return None
    return parsed.isoformat()


def parse_time(value):
    value = clean_text(value).lower().replace('.', ':').replace(' ', '')
    match = re.search(r'(\d{1,2})(?::(\d{2}))?(am|pm)', value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 12 or minute > 59:
        return None
    if match.group(3) == 'pm' and hour != 12:
        hour += 12
    elif match.group(3) == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def resolve_city(venue):
    venue = clean_text(venue)
    if not venue:
        return None
    lowered = venue.lower()
    if any(term in lowered for term in HOME_VENUE_TERMS):
        return 'Gateshead'

    # Touring performances normally include the locality after the final
    # comma (for example "St Mary's Parish Church, Haddington").
    if ',' in venue:
        city = clean_text(venue.rsplit(',', 1)[1])
        if city and not re.search(r'\b(room|hall|one|two|stage|auditorium)\b', city, re.I):
            return city
    return None


def page_description(soup):
    parts = []
    for block in soup.select('.c-page__content .c-col-content'):
        text = clean_text(block)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def performance_records(item, soup):
    title = clean_text(soup.select_one('.c-event-masthead__title--desktop h2')) or item['title']
    subtitle = clean_text(soup.select_one('.c-event-masthead__title--desktop .c-event-masthead__subtitle'))
    if subtitle and subtitle.lower() not in title.lower():
        title = f'{title} – {subtitle}'
    description = page_description(soup)
    records = []

    performances = soup.select('.c-event-perf')
    for performance in performances:
        event_date = parse_date(performance.select_one('.c-event-perf__date'))
        venue = clean_text(performance.select_one('.c-event-perf__venue span'))
        city = resolve_city(venue)
        time_from = parse_time(performance.select_one('time'))
        if not title or not event_date or not venue or not city:
            continue
        records.append(make_record(title, event_date, time_from, venue, city, item['url'], description))

    if records:
        return records

    # Some free events have no ticket/performance block, but still expose one
    # unambiguous date and venue in their listing card.
    date_match = re.search(
        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+'
        r'\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}',
        item['date_text'],
    )
    event_date = parse_date(date_match.group(0)) if date_match else None
    venue = item['venue']
    city = resolve_city(venue)
    if title and event_date and venue and city:
        return [make_record(
            title, event_date, parse_time(item['date_text']), venue, city, item['url'], description
        )]
    return []


def make_record(title, event_date, time_from, venue, city, url, description):
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def scrape_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = listing_items(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_soup, session, item['url']): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                records.extend(performance_records(item, future.result()))
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'] or '', record['title'], record['venue']),
    )


class TheGlasshouseIcmOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='theglasshouseicm_org',
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
        return scrape_concerts()


def main():
    TheGlasshouseIcmOrgCrawler().run()


if __name__ == '__main__':
    main()
