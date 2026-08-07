import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://resoundcollective.org/'
EVENTS_URL = urljoin(SOURCE_URL, 'events/')
PAST_EVENTS_URL = urljoin(SOURCE_URL, 'past-events/')
SOURCE = 'Resound Collective'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-SG,en;q=0.9',
}

MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        ('January', 'February', 'March', 'April', 'May', 'June',
         'July', 'August', 'September', 'October', 'November', 'December'),
        1,
    )
}
MONTH_PATTERN = '|'.join(list(MONTHS) + [name[:3] for name in MONTHS])
WEEKDAY_PATTERN = r'(?:mon(?:day)?|tue(?:sday|s)?|wed(?:nesday)?|thu(?:rsday|rs)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)'


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def month_number(value):
    value = value.lower()
    for name, number in MONTHS.items():
        if name.startswith(value[:3]):
            return number
    return None


def parse_dates(value):
    """Return every explicitly published calendar date in a grid excerpt."""
    text = re.sub(rf'(?i)\b{WEEKDAY_PATTERN}\b,?', ' ', value)
    text = re.sub(r'\s+', ' ', text).strip()
    year_matches = re.findall(r'\b(20\d{2})\b', text)
    if not year_matches:
        return []
    default_year = int(year_matches[-1])
    found = set()

    # Ordinary dates, including multiple complete dates in one excerpt.
    pattern = rf'\b(\d{{1,2}})\s+({MONTH_PATTERN})\s*(20\d{{2}})?\b'
    for match in re.finditer(pattern, text, re.I):
        day = int(match.group(1))
        month = month_number(match.group(2))
        year = int(match.group(3) or default_year)
        try:
            found.add(datetime(year, month, day).date())
        except ValueError:
            continue

    # Forms such as "25 & 26 Sep" and "20/21 October".
    shared_month = rf'\b(\d{{1,2}})\s*(?:&|/|and)\s*(\d{{1,2}})\s+({MONTH_PATTERN})\s*(20\d{{2}})?\b'
    for match in re.finditer(shared_month, text, re.I):
        month = month_number(match.group(3))
        year = int(match.group(4) or default_year)
        for raw_day in match.group(1), match.group(2):
            try:
                found.add(datetime(year, month, int(raw_day)).date())
            except ValueError:
                pass

    # Short inclusive runs such as "27-30 June 2024".
    date_range = rf'\b(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\s+({MONTH_PATTERN})\s*(20\d{{2}})?\b'
    for match in re.finditer(date_range, text, re.I):
        start_day, end_day = int(match.group(1)), int(match.group(2))
        month = month_number(match.group(3))
        year = int(match.group(4) or default_year)
        if end_day - start_day > 14:
            continue
        try:
            current = datetime(year, month, start_day).date()
            end = datetime(year, month, end_day).date()
        except ValueError:
            continue
        while current <= end:
            found.add(current)
            current += timedelta(days=1)

    return [date.isoformat() for date in sorted(found)]


def parse_time(value):
    match = re.search(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', value, re.I)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if hour > 12 or minute > 59:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def extract_venue(excerpt):
    lines = [line.strip(' ,') for line in excerpt.splitlines() if line.strip(' ,')]
    candidates = []
    for line in lines:
        if re.search(r'\b20\d{2}\b', line) or parse_time(line):
            continue
        if re.fullmatch(rf'(?i){WEEKDAY_PATTERN}(?:/{WEEKDAY_PATTERN})*', line):
            continue
        if re.search(r'(?i)save the dates|jan\s*[–-]\s*dec', line):
            continue
        candidates.append(line)
    if len(candidates) == 1 and re.fullmatch(r'(?i)kuala lumpur\s*,\s*melaka', candidates[0]):
        # This archive card describes a multi-stop tour, not an actual venue.
        # Its detail page mixes concerts, workshops and different locations, so
        # it cannot safely be expanded from the card's summary.
        return None
    # Some venues are deliberately split over two lines (for example the hall
    # name followed by "@ WILD RICE"). Preserve both rather than guessing.
    return ', '.join(candidates) if 0 < len(candidates) <= 2 else None


def detail_description(session, url):
    soup = get_soup(session, url)
    content = soup.select_one('.entry-content') or soup.select_one('main')
    description = clean_text(content)
    return description or None


def listing_items(session):
    items = {}
    for listing_url in (EVENTS_URL, PAST_EVENTS_URL):
        soup = get_soup(session, listing_url)
        for card in soup.select('.vc_grid-item'):
            link = card.select_one('a.vc_gitem-link[href]')
            title = clean_text(card.select_one('.vc_gitem-post-data-source-post_title'))
            excerpt = clean_text(card.select_one('.vc_gitem-post-data-source-post_excerpt'))
            if not link or not title or not excerpt:
                continue
            url = urljoin(SOURCE_URL, link['href'])
            if urlparse(url).netloc == urlparse(SOURCE_URL).netloc:
                items[url] = (title, excerpt)
    return items


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = listing_items(session)
    descriptions = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(detail_description, session, url): url for url in items}
        for future in as_completed(futures):
            url = futures[future]
            try:
                descriptions[url] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Resound Collective event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                descriptions[url] = None

    records = []
    for url, (title, excerpt) in items.items():
        dates = parse_dates(excerpt)
        venue = extract_venue(excerpt)
        if not dates or not venue:
            continue
        country_code = 'MY' if re.search(r'(?i)malaysia|kuala lumpur|melaka', venue) else 'SG'
        city = 'Kuala Lumpur' if re.search(r'(?i)kuala lumpur', venue) else 'Melaka' if re.search(r'(?i)melaka', venue) else 'Singapore'
        for event_date in dates:
            records.append({
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': parse_time(excerpt),
                'venue': venue,
                'city': city,
                'country_code': country_code,
                'description': descriptions.get(url),
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return sorted(records, key=lambda row: (row['date'], row['time_from'] or '', row['title']))


class ResoundCollectiveOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='resoundcollective_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='SG',
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
    ResoundCollectiveOrgCrawler().run()


if __name__ == '__main__':
    main()
