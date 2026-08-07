import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://janacek-brno.cz/'
PROGRAM_URL = urljoin(SOURCE_URL, 'program-festivalu/')
SOURCE = 'Janáček Brno'
HOME_CITY = 'Brno'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'cs-CZ,cs;q=0.9,en;q=0.7',
}

DATE_TIME_RE = re.compile(
    r'(?P<day>\d{1,2})\.\s*(?P<month>\d{1,2})\.\s*(?P<year>20\d{2})'
    r'\s*,?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})'
)
NON_VENUE_RE = re.compile(
    r'^(?:premi[eé]ra|derni[eé]ra|veřejná generálka|klavírní generálka|'
    r'vyprodáno|poslední volné vstupenky|délka\b|další termíny\b)',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    value = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def parse_listing_title(value):
    value = clean_text(value).replace('\n', ' ')
    match = DATE_TIME_RE.search(value)
    if not match:
        return None
    title = value[match.end():].strip(' |–-')
    return title or None


def parse_datetime(value):
    match = DATE_TIME_RE.search(clean_text(value))
    if not match:
        return None, None
    values = {key: int(number) for key, number in match.groupdict().items()}
    try:
        event_date = date(values['year'], values['month'], values['day']).isoformat()
    except ValueError:
        return None, None
    if values['hour'] > 23 or values['minute'] > 59:
        return None, None
    return event_date, f"{values['hour']:02d}:{values['minute']:02d}"


def get_soup(session, url):
    response = session.get(url, timeout=40)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_items(soup):
    items = []
    seen = set()
    for article in soup.select('article.category-2026-program'):
        link = article.select_one('h3.entry-title a[href]')
        if not link:
            continue
        url = urljoin(PROGRAM_URL, link.get('href'))
        if url in seen:
            continue
        seen.add(url)

        listing_text = clean_text(link.get_text(' ', strip=True))
        event_date, time_from = parse_datetime(listing_text)
        title = parse_listing_title(listing_text)
        if event_date and title:
            items.append(
                {
                    'title': title,
                    'date': event_date,
                    'time_from': time_from,
                    'url': url,
                }
            )
    return items


def extract_venue(entry):
    datetime_heading = next(
        (
            heading
            for heading in entry.select('h2')
            if DATE_TIME_RE.search(clean_text(heading.get_text(' ', strip=True)))
        ),
        None,
    )
    if not datetime_heading:
        return None

    for paragraph in datetime_heading.find_all_next('p', limit=10):
        text = clean_text(paragraph.get_text(' ', strip=True))
        if not text or ':' in text or NON_VENUE_RE.search(text):
            continue
        # Venue labels on this site are short and bold. This avoids treating
        # narrative text, performers, or ticket information as a venue.
        if paragraph.find('strong') and len(text) <= 100:
            return text
    return None


def extract_description(entry):
    parts = []
    seen = set()
    for wrapper in entry.select('.wpb_text_column > .wpb_wrapper'):
        for unwanted in wrapper.select('script, style, form, button'):
            unwanted.decompose()
        text = clean_text(wrapper.get_text('\n', strip=True))
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(text)
    return clean_text('\n\n'.join(parts)) or None


def detail_record(session, item):
    soup = get_soup(session, item['url'])
    entry = soup.select_one('article.single-postlike .entry-content')
    if not entry:
        return None
    venue = extract_venue(entry)
    if not venue:
        return None
    return {
        **item,
        'venue': venue,
        'city': HOME_CITY,
        'country_code': 'CZ',
        'description': extract_description(entry),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = listing_items(get_soup(session, PROGRAM_URL))
    records = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(detail_record, session, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                record = future.result()
                if record:
                    records.append(record)
                else:
                    log_message(
                        'Skipping event without a parseable venue',
                        event='crawler_item_skipped',
                        level='warning',
                        url=item['url'],
                    )
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class JanacekBrnoCzCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='janacek_brno_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
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
    JanacekBrnoCzCrawler().run()


if __name__ == '__main__':
    main()
