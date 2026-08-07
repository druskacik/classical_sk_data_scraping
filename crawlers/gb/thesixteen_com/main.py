import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://thesixteen.com/'
SOURCE = 'The Sixteen'
EVENTS_API = f'{SOURCE_URL}wp-json/wp/v2/event'
PERFORMANCE_CATEGORY = 9

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# The Sixteen tours internationally. Titles consistently name the tour city;
# these explicit exceptions prevent foreign dates being assigned to GB.
FOREIGN_CITIES = {
    'Amsterdam': 'NL',
    'Athens, Georgia': 'US',
    'Birmingham, Alabama': 'US',
    'Cunewalde': 'DE',
    'Dallas, Texas': 'US',
    'Dublin': 'IE',
    'Fukuoka': 'JP',
    'Gent': 'BE',
    'Groningen': 'NL',
    'Hertogenbosch': 'NL',
    'Johnson City, Tennessee': 'US',
    'Katowice': 'PL',
    'Kyoto': 'JP',
    'Lessay': 'FR',
    'Madrid': 'ES',
    'New Haven, Connecticut': 'US',
    'New York': 'US',
    'Nijmegen': 'NL',
    'Oslo': 'NO',
    'Rotterdam': 'NL',
    'St Louis': 'US',
    'Stockholm': 'SE',
    'Tenerife': 'ES',
    'Tokyo': 'JP',
    'Valencia': 'ES',
    'Warsaw': 'PL',
    'Yokohama': 'JP',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = html.unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def build_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    session.mount('https://', HTTPAdapter(max_retries=retry))
    return session


def performance_urls(session):
    params = {
        'event_category': PERFORMANCE_CATEGORY,
        'per_page': 100,
        'page': 1,
        '_fields': 'link',
    }
    urls = []
    while True:
        response = session.get(EVENTS_API, params=params, timeout=45)
        response.raise_for_status()
        urls.extend(item['link'] for item in response.json() if item.get('link'))
        total_pages = int(response.headers.get('X-WP-TotalPages', '1'))
        if params['page'] >= total_pages:
            break
        params['page'] += 1
    return list(dict.fromkeys(urls))


def parse_date(value):
    text = clean_text(value)
    match = re.search(r'\b([0-3]?\d)\s+([A-Za-z]+),?\s+(20\d{2})\b', text)
    if not match:
        return None
    try:
        return datetime.strptime(' '.join(match.groups()), '%d %B %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2})(?:[.:]([0-5]\d))?\s*(am|pm)\b', clean_text(value), re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'pm':
        hour += 12
    return f'{hour:02d}:{match.group(2) or "00"}'


def city_from_address(soup):
    address = soup.select_one('.address')
    if not address:
        return None
    lines = [line.strip(' ,') for line in clean_text(address).splitlines() if line.strip(' ,')]
    for index, line in enumerate(lines):
        if re.search(r'\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b', line, re.I):
            return lines[index - 1] if index else None
    return None


def resolve_location(title, soup):
    city = title.rsplit(':', 1)[1].strip() if ':' in title else city_from_address(soup)
    if not city or len(city) > 40 or re.search(r'workshop|concert|showcase|choir|sing|performances', city, re.I):
        city = city_from_address(soup)
    if not city:
        return None, None
    return city, FOREIGN_CITIES.get(city, 'GB')


def description_from(soup):
    sections = list(soup.select('section.main-content-block'))
    sections.extend(
        node for node in soup.select('.programme-list')
        if 'preformers-list' not in (node.get('class') or [])
    )
    parts = []
    for section in sections:
        text = clean_text(section)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_detail(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    header = soup.select_one('.banner-title .titles')
    if not header:
        return None
    title = clean_text(header.select_one('h1'))
    event_date = parse_date(header.select_one('.date'))
    venue_node = next(
        (node for node in header.select('h3') if clean_text(node).lower().startswith('venue:')),
        None,
    )
    venue = re.sub(r'^venue:\s*', '', clean_text(venue_node), flags=re.I).strip()
    city, country_code = resolve_location(title, soup)
    if not title or not event_date or not venue or not city or not country_code:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': response.url,
        'time_from': parse_time(header.select_one('.time')),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description_from(soup),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = build_session()
    urls = performance_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(parse_detail, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape The Sixteen concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(records, key=lambda row: (row['date'], row['time_from'] or '', row['title']))


class ThesixteenComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='thesixteen_com',
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
    ThesixteenComCrawler().run()


if __name__ == '__main__':
    main()
