import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.1901artsclub.com/'
SITEMAP_URL = f'{SOURCE_URL}sitemap.xml'
SOURCE = '1901 Arts Club'
VENUE = '1901 Arts Club'
CITY = 'London'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

MONTHS = (
    'January|February|March|April|May|June|July|August|September|October|'
    'November|December'
)
DATE_RE = re.compile(
    rf'\b(\d{{1,2}})(?:\s*(?:&|and)\s*(\d{{1,2}}))?\s+({MONTHS})\s+(20\d{{2}})\b',
    re.IGNORECASE,
)
TIME_RE = re.compile(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', re.IGNORECASE)


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def event_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    urls = []
    for node in soup.select('url > loc'):
        url = clean_text(node)
        path = urlparse(url).path.rsplit('/', 1)[-1]
        if re.match(r'^\d{1,2}[-]', path):
            urls.append(url)
    return list(dict.fromkeys(urls))


def parse_dates(text):
    dates = []
    for match in DATE_RE.finditer(text):
        first_day, second_day, month, year = match.groups()
        for day in (first_day, second_day):
            if not day:
                continue
            try:
                value = datetime.strptime(f'{day} {month} {year}', '%d %B %Y').date().isoformat()
            except ValueError:
                continue
            if value not in dates:
                dates.append(value)
    return dates


def normalise_time(match):
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour < 1 or hour > 12 or minute > 59:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def parse_time(text):
    labelled = re.search(
        r'\b(?:concert|performance|start(?:s)?)\s*:\s*'
        r'(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b',
        text,
        re.IGNORECASE,
    )
    if labelled:
        return normalise_time(labelled)
    return None


def description_text(content):
    parts = []
    for node in content.select('h2, .paragraph'):
        text = clean_text(node)
        if not text or text in parts:
            continue
        if re.match(r'^(?:doors|concert|duration|tickets?)\s*:', text, re.IGNORECASE):
            continue
        parts.append(text)
    return '\n\n'.join(parts[1:]) or None


def parse_event(content, url):
    soup = BeautifulSoup(content, 'html.parser')
    main_content = soup.select_one('#wsite-content')
    if not main_content:
        return []

    title_node = main_content.select_one('h1, h2.wsite-content-title, h2')
    title = clean_text(title_node)
    page_text = clean_text(main_content)
    url_year_match = re.search(r'-(20\d{2})(?:-|\.)', urlparse(url).path)
    url_year = url_year_match.group(1) if url_year_match else None
    dates = [
        event_date
        for event_date in parse_dates(page_text)
        if not url_year or event_date.startswith(url_year)
    ]
    if not title or not dates:
        return []

    time_from = parse_time(page_text)
    description = description_text(main_content)
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': VENUE,
            'city': CITY,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date in dates
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_event(future.result().content, url))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape 1901 Arts Club event detail',
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


class ArtsClub1901ComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='1901artsclub_com',
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
    ArtsClub1901ComCrawler().run()


if __name__ == '__main__':
    main()
