import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.thebrigantes.uk/'
LISTING_URL = urljoin(SOURCE_URL, 'concerts-907601-179166.html')
SOURCE = 'The Brigantes Orchestra'
CITY = 'Sheffield'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

DATE_RE = re.compile(
    r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+'
    r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|'
    r'September|October|November|December)\s+(\d{4})\b',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(text):
    match = DATE_RE.search(text or '')
    if not match:
        return None
    try:
        return datetime.strptime(' '.join(match.groups()), '%d %B %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(text):
    text = text or ''
    match = re.search(r'\bbegins at\s*(\d{1,2})(?::|\.?)(\d{2})\s*hrs\b', text, re.I)
    if match:
        hour, minute = map(int, match.groups())
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f'{hour:02d}:{minute:02d}'

    match = re.search(r'\b(\d{1,2})[.:](\d{2})\s*(am|pm)\b', text, re.I)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def listing_items(session):
    response = session.get(LISTING_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    items = []
    seen = set()
    for link in soup.select('a[href]'):
        if 'MORE INFO' not in clean_text(link).upper():
            continue
        url = urljoin(LISTING_URL, link.get('href'))
        if url in seen:
            continue
        text = ''
        for table in link.find_parents('table', class_='wsite-multicol-table'):
            candidate = clean_text(table)
            if parse_date(candidate):
                text = candidate
                break
        if not text:
            continue
        seen.add(url)
        items.append({'url': url, 'listing_text': text})
    return items


def venue_from_text(text):
    for line in (text or '').splitlines():
        line = line.strip()
        if 'cathedral' in line.lower():
            line = DATE_RE.sub('', line).strip(' ,-')
            line = re.split(r'\s*\(note venue change\)', line, flags=re.I)[0].strip()
            if line:
                return line
    return None


def listing_record(item):
    text = item['listing_text']
    event_date = parse_date(text)
    date_match = DATE_RE.search(text)
    title = text[:date_match.start()].strip() if date_match else ''
    title = re.sub(r'^Picture\s*', '', title, flags=re.I).strip()
    venue = venue_from_text(text)
    if not title or not event_date or not venue:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': item['url'],
        'time_from': parse_time(text),
        'venue': venue,
        'city': CITY,
        'country_code': 'GB',
        'description': text or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def detail_record(session, item):
    response = session.get(item['url'], timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    content = soup.select_one('#wsite-content')
    if not content:
        return listing_record(item)
    text = clean_text(content)
    event_date = parse_date(text) or parse_date(item['listing_text'])

    heading = content.find(['h1', 'h2'])
    heading_text = clean_text(heading)
    title = DATE_RE.sub('', heading_text).strip(' -|\n') if heading_text else ''
    if not title:
        title = clean_text(soup.title).removesuffix(' - THE BRIGANTES').strip()
    title = re.sub(r'\s+', ' ', title)

    venue = venue_from_text(text) or venue_from_text(item['listing_text'])
    if venue and venue.lower() == 'cathedral, sheffield':
        venue = venue_from_text(item['listing_text']) or 'Sheffield Cathedral'

    if not title or not event_date or not venue:
        return listing_record(item)
    return {
        'title': title,
        'date': event_date,
        'url': item['url'],
        'time_from': parse_time(text) or parse_time(item['listing_text']),
        'venue': venue,
        'city': CITY,
        'country_code': 'GB',
        'description': text or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = listing_items(session)
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(detail_record, session, item): item for item in items}
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
    return sorted(records, key=lambda record: (record['date'], record['time_from'] or '', record['title']))


class TheBrigantesUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='thebrigantes_uk',
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
    TheBrigantesUkCrawler().run()


if __name__ == '__main__':
    main()
