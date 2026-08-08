import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.brittensinfonia.com/'
EVENTS_URL = f'{SOURCE_URL}events'
SOURCE = 'Britten Sinfonia'

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
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_urls(session):
    # The date picker disallows past dates in the browser, but the server accepts
    # an earlier date and returns every event it still retains, including archives.
    soup = get_soup(session, EVENTS_URL, params={'date': '1900-01-01'})
    urls = []
    for card in soup.select('a.event-listing[href]'):
        categories = {
            clean_text(tag).casefold()
            for tag in card.select('.event-listing__event-type')
        }
        # Community workshops and schools projects share this calendar. Retain
        # concert, family-concert and festival performances, plus uncategorised
        # public performances, while excluding community-only activities.
        if categories and not categories.intersection(
            {'concerts', 'family', 'summer festivals'}
        ):
            continue
        url = card.get('href', '').strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def description_from(soup):
    parts = []
    for heading in soup.find_all(['h2', 'h3']):
        if clean_text(heading).casefold() != 'programme':
            continue
        section = heading.find_parent(class_='flex--wrap')
        programme = section.select_one('.detail-list') if section else None
        text = clean_text(programme)
        if text:
            parts.append(f'Programme\n{text}')
        break

    for element in soup.select('main .intro-text, main .typeset.mb-80'):
        text = clean_text(element)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_performance(title, url, description, performance):
    city = clean_text(performance.select_one('.performance__location'))
    venue = clean_text(performance.select_one('.performance__venue'))
    venue_parts = [part.strip() for part in venue.split(',') if part.strip()]
    # One listing uses the county as its location label while naming the actual
    # town in the venue. Prefer that stronger city evidence.
    if city.casefold() == 'sussex' and any(
        part.casefold() == 'rye' for part in venue_parts[1:]
    ):
        city = 'Rye'
    # Text after the first comma is an address or duplicated locality, neither
    # of which belongs in the venue field.
    if venue_parts:
        venue = venue_parts[0]
    date_time = clean_text(performance.select_one('.performance__date-time'))
    match = re.search(
        r'(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})(?:\s+at\s+(\d{1,2}(?::\d{2})?\s*[ap]m))?',
        date_time,
        re.I,
    )
    if not all((title, url, city, venue, match)):
        return None
    try:
        event_date = datetime.strptime(match.group(1), '%d %b %Y').date().isoformat()
    except ValueError:
        return None

    time_from = None
    if match.group(2):
        raw_time = re.sub(r'\s+', '', match.group(2)).upper()
        for pattern in ('%I:%M%p', '%I%p'):
            try:
                time_from = datetime.strptime(raw_time, pattern).strftime('%H:%M')
                break
            except ValueError:
                pass

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


def parse_event(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.select_one('h1.hero__title'))
    description = description_from(soup)
    records = []
    for performance in soup.select('.performance'):
        record = parse_performance(title, url, description, performance)
        if record:
            records.append(record)
        else:
            log_message(
                'Skipped incomplete Britten Sinfonia performance',
                event='crawler_item_skipped',
                level='warning',
                url=url,
                error_type='IncompleteEventData',
                error_message='Required title, date, venue, or city is missing',
            )
    return records


class BrittenSinfoniaComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='brittensinfonia_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        urls = listing_urls(session)
        records = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(parse_event, session, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    records.extend(future.result())
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape Britten Sinfonia event',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(
            records,
            key=lambda item: (
                item['date'], item['time_from'] or '', item['title'], item['venue']
            ),
        )


def main():
    BrittenSinfoniaComCrawler().run()


if __name__ == '__main__':
    main()
