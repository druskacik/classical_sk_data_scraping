import calendar
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.altenburg-arts.com/'
SOURCE = 'Altenburg Arts'
INDEX_URLS = (SOURCE_URL, f'{SOURCE_URL}past-concerts')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-SG,en;q=0.9',
}

MONTHS = {name.upper(): number for number, name in enumerate(calendar.month_name) if name}
MONTH_PATTERN = '|'.join(MONTHS)
DATE_PATTERN = re.compile(
    rf'(?:(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)\s+)?'
    rf'(?P<days>\d{{1,2}}(?:\s*(?:,|&|–|-)\s*\d{{1,2}})*)\s+'
    rf'(?P<month>{MONTH_PATTERN})\s+(?P<year>20\d{{2}})',
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\b', re.IGNORECASE)
VENUE_PATTERN = re.compile(
    r'\b(?:HALL|THEATRE|THEATER|AUDITORIUM|ARTS HOUSE|ESPLANADE|SOTA)\b',
    re.IGNORECASE,
)
NON_EVENT_PATHS = {
    '', 'projects-event', 'past-concerts', 'altenburg-arts-digital',
    'subscription', 'about-us', 'contact', 'privacy-policy',
    'personal-data-protection', 'terms-and-conditions',
}


def clean_text(value):
    if not value:
        return ''
    text = str(value).replace('\xa0', ' ').replace('\u200b', '').replace('\ufeff', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.text


def detail_links(html):
    soup = BeautifulSoup(html, 'html.parser')
    links = set()
    for link in soup.select('main a[href], a[href]'):
        href = link.get('href', '').split('#', 1)[0].rstrip('/')
        parsed = urlparse(href)
        if parsed.netloc not in ('altenburg-arts.com', 'www.altenburg-arts.com'):
            continue
        path = parsed.path.strip('/')
        if path and path not in NON_EVENT_PATHS:
            links.add(f'{SOURCE_URL}{path}')
    return links


def expand_days(value):
    numbers = [int(item) for item in re.findall(r'\d{1,2}', value)]
    if len(numbers) == 2 and re.search(r'[–-]', value):
        start, end = numbers
        if start <= end and end - start <= 7:
            return list(range(start, end + 1))
    return numbers


def parse_time(value):
    match = TIME_PATTERN.search(value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    if match.group(3).upper() == 'PM' and hour != 12:
        hour += 12
    elif match.group(3).upper() == 'AM' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def venue_near(lines, date_index, date_match):
    suffix = clean_text(lines[date_index][date_match.end():]).lstrip(' ,–-')
    candidates = ([suffix] if suffix else []) + lines[date_index + 1:date_index + 6]
    for candidate in candidates:
        candidate = clean_text(candidate)
        if VENUE_PATTERN.search(candidate) and len(candidate) <= 100:
            candidate = re.sub(r'^(?:AT|VENUE)\s*:?\s*', '', candidate, flags=re.IGNORECASE)
            candidate = re.sub(
                r'^\d{1,2}(?::\d{2})?\s*(?:AM|PM)\s*,?\s*',
                '',
                candidate,
                flags=re.IGNORECASE,
            )
            return candidate.strip(' ,–-')
    return ''


def title_from_page(soup):
    title = clean_text(soup.title.get_text(' ', strip=True) if soup.title else '')
    title = re.sub(r'\s*\|\s*Altenburg Arts.*$', '', title, flags=re.IGNORECASE)
    if title.lower() == 'classical music concerts':
        heading = soup.select_one('main h1, main h2, main h3')
        title = clean_text(heading.get_text(' ', strip=True) if heading else title)
    return title


def parse_detail(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    main = soup.select_one('main') or soup.body
    if main is None:
        return []
    lines = [clean_text(value) for value in main.stripped_strings]
    lines = [value for value in lines if value and value != '\u200b']
    title = title_from_page(soup)
    description = clean_text('\n'.join(lines)) or None
    if not title or not description:
        return []
    header_text = ' '.join(lines[:30]).lower()
    if 'cancelled' in header_text or 'postponed' in header_text:
        return []

    records = []
    # Event metadata is in the page header. Limiting the scan avoids treating
    # dates in artist biographies and press quotations as performances.
    for index, line in enumerate(lines[:45]):
        match = DATE_PATTERN.search(line)
        if not match:
            continue
        matched_date = match.group(0)
        if matched_date.upper() != matched_date:
            continue
        context = ' '.join(lines[max(0, index - 2):index + 2]).lower()
        if 'cancelled' in context or 'postponed' in context:
            continue
        venue = venue_near(lines, index, match)
        if not venue:
            continue
        nearby = ' '.join(lines[index:index + 5])
        time_from = parse_time(nearby)
        is_shanghai = 'shanghai' in f'{venue} {soup.title.get_text(" ", strip=True)}'.lower()
        city = 'Shanghai' if is_shanghai else 'Singapore'
        country_code = 'CN' if is_shanghai else 'SG'
        month = MONTHS[match.group('month').upper()]
        year = int(match.group('year'))
        for day in expand_days(match.group('days')):
            try:
                event_date = date(year, month, day).isoformat()
            except ValueError:
                continue
            records.append({
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': country_code,
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    links = set()
    for index_url in INDEX_URLS:
        links.update(detail_links(fetch(session, index_url)))

    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch, session, url): url for url in sorted(links)}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_detail(future.result(), url))
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Altenburg Arts concert page',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    unique = {(item['url'], item['date'], item['time_from'], item['venue']): item for item in records}
    return sorted(unique.values(), key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class AltenburgArtsComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='altenburg_arts_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='SG',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    AltenburgArtsComCrawler().run()


if __name__ == '__main__':
    main()
