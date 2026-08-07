import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://oxfordphil.com/'
SOURCE = 'Oxford Philharmonic Orchestra'
SITEMAPS = (
    f'{SOURCE_URL}event-sitemap.xml',
    f'{SOURCE_URL}additional_event-sitemap.xml',
)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2,
    'mar': 3, 'march': 3, 'apr': 4, 'april': 4, 'may': 5,
    'jun': 6, 'june': 6, 'jul': 7, 'july': 7, 'aug': 8,
    'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10, 'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}

# Most events are in Oxford, but the archive also contains touring concerts.
# Only use the home-city default for recognisable Oxford locations.
OXFORD_LOCATION_MARKERS = (
    'oxford', 'sheldonian theatre', 'holywell music room', 'oxford town hall',
    'new theatre', 'st hilda', 'st john’s college', "st john's college",
    'merton college', 'christ church', 'university church', 'magdalen college',
    'blenheim palace', 'dorchester abbey', 'saïd business school',
)

TOUR_CITIES = {
    'london': ('London', 'GB'),
    'kanazawa': ('Kanazawa', 'JP'),
    'fukui': ('Fukui', 'JP'),
    'tokyo': ('Tokyo', 'JP'),
    'nagoya': ('Nagoya', 'JP'),
    'new york': ('New York', 'US'),
    'rome': ('Rome', 'IT'),
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, parser='html.parser'):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, parser)


def event_urls(session):
    urls = []
    for sitemap_url in SITEMAPS:
        soup = get_soup(session, sitemap_url, 'xml')
        for element in soup.find_all('loc'):
            url = clean_text(element)
            path = urlparse(url).path
            if re.match(r'^/(?:event|additional_event)/[^/]+/?$', path):
                urls.append(url)
    return list(dict.fromkeys(urls))


def metadata_text(soup):
    subtitle = soup.select_one('.event-subtitle')
    if subtitle and clean_text(subtitle).count('|') >= 2:
        return clean_text(subtitle)

    article = soup.select_one('.article-body') or soup
    for element in article.select('h2, h3, p'):
        text = clean_text(element)
        if text.count('|') >= 2 and re.search(r'\b\d{4}\b', text):
            return text
    return ''


def parse_dates(text):
    year_match = re.search(r'\b(20\d{2})\b', text)
    month_match = re.search(
        r'\b(January|February|March|April|May|June|July|August|September|Sept|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b',
        text,
        re.IGNORECASE,
    )
    if not year_match or not month_match:
        return []
    prefix = text[:month_match.start()]
    days = [int(value) for value in re.findall(r'\b([0-3]?\d)\b', prefix)]
    if not days:
        return []
    month = MONTHS[month_match.group(1).lower()]
    year = int(year_match.group(1))
    parsed = []
    for day in dict.fromkeys(days):
        try:
            parsed.append(date(year, month, day).isoformat())
        except ValueError:
            continue
    return parsed


def parse_time(text):
    match = re.search(r'\|\s*(\d{1,2})[:.]([0-5]\d)\b', text)
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def resolve_location(text):
    parts = [clean_text(part) for part in text.split('|')]
    venue = parts[2] if len(parts) >= 3 else ''
    venue = re.sub(r'^Venue:\s*', '', venue, flags=re.IGNORECASE).strip(' ,')
    if not venue or ';' in venue:
        return None, None, None

    location = re.sub(r'\s+', ' ', venue).lower()
    if any(marker in location for marker in OXFORD_LOCATION_MARKERS):
        return venue, 'Oxford', 'GB'

    # Touring pages normally include a recognisable city in the venue string.
    # Use an explicit map so an address, county, or country is never emitted as
    # the city merely because it follows the final comma.
    for marker, (city, country_code) in TOUR_CITIES.items():
        if re.search(rf'\b{re.escape(marker)}\b', location):
            return venue, city, country_code
    return None, None, None


def description_from(soup):
    description = soup.select_one('.event-description')
    if description:
        return clean_text(description) or None

    article = soup.select_one('.article-body')
    if not article:
        return None
    # Additional events use flexible WordPress blocks. Stop before booking and
    # related-event sections, while retaining programme and narrative blocks.
    parts = []
    for element in article.select('p, ul, ol'):
        if element.find_parent('article', class_='tease'):
            continue
        text = clean_text(element)
        if not text or re.search(r'^(Tickets?:|Book Tickets|Share Our Event)', text, re.I):
            continue
        if text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_detail(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.select_one('.event-title')) or clean_text(soup.select_one('h1'))
    metadata = metadata_text(soup)
    dates = parse_dates(metadata)
    venue, city, country_code = resolve_location(metadata)
    if not title or not dates or not venue or not city:
        return []
    description = description_from(soup)
    time_from = parse_time(metadata)
    return [{
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
    } for event_date in dates]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    session.mount('https://', HTTPAdapter(max_retries=retry))
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(parse_detail, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(records, key=lambda row: (row['date'], row['time_from'] or '', row['title']))


class OxfordphilComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='oxfordphil_com',
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
    OxfordphilComCrawler().run()


if __name__ == '__main__':
    main()
