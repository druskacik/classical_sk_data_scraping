import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html import unescape

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.bremf.org.uk/'
SITEMAP_URL = f'{SOURCE_URL}event-sitemap.xml'
SOURCE = 'Brighton Early Music Festival'
DEFAULT_CITY = 'Brighton'

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
    text = unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def get_event_urls(session):
    response = session.get(SITEMAP_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'xml')
    return list(
        dict.fromkeys(
            clean_text(location)
            for location in soup.select('url > loc')
            if clean_text(location).rstrip('/') != f'{SOURCE_URL}event'
        )
    )


def schema_nodes(soup):
    nodes = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or '')
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            nodes.extend(value.get('@graph', [value]))
    event = next((node for node in nodes if node.get('@type') == 'Event'), {})
    webpage = next((node for node in nodes if node.get('@type') == 'WebPage'), {})
    return event, webpage


def labeled_value(soup, label):
    pattern = re.compile(rf'^\s*{re.escape(label)}\s*:', re.I)
    for strong in soup.select('article strong'):
        if pattern.search(clean_text(strong)):
            parent = strong.parent
            return pattern.sub('', clean_text(parent), count=1).strip()
    return ''


def visible_date_text(soup):
    value = labeled_value(soup, 'Date & Time')
    if value:
        return value
    heading = soup.select_one('article h4')
    return re.split(r'\s+at\s+', clean_text(heading), maxsplit=1, flags=re.I)[0]


def parse_event_datetime(value, published_at, schema_start):
    match = re.search(
        r'\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+'
        r'(\d{1,2})\s+([A-Za-z]+)(?:\s+(20\d{2}))?'
        r'(?:\s*,?\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?)?',
        value,
        re.I,
    )
    if not match:
        return None, None

    weekday, day, month, explicit_year, hour, minute, meridiem = match.groups()
    try:
        month_number = datetime.strptime(month, '%B').month
    except ValueError:
        try:
            month_number = datetime.strptime(month, '%b').month
        except ValueError:
            return None, None

    published = None
    try:
        published = datetime.fromisoformat(published_at.replace('Z', '+00:00')).date()
    except (TypeError, ValueError):
        pass

    schema_year = None
    try:
        schema_year = datetime.fromisoformat(schema_start).year
    except (TypeError, ValueError):
        pass

    if explicit_year:
        years = [int(explicit_year)]
    elif published:
        years = range(published.year - 1, published.year + 3)
    elif schema_year:
        years = [schema_year]
    else:
        return None, None

    candidates = []
    for year in years:
        try:
            candidate = date(year, month_number, int(day))
        except ValueError:
            continue
        if candidate.strftime('%A').lower() != weekday.lower():
            continue
        # Pages from old festivals currently carry a bogus 2026 JSON-LD year.
        # Publication time remains a reliable anchor for their visible dates.
        distance = abs((candidate - published).days) if published else 0
        candidates.append((distance, candidate))
    if not candidates:
        return None, None
    event_date = min(candidates)[1]

    time_from = None
    if hour:
        hour_number = int(hour)
        if meridiem:
            hour_number = hour_number % 12 + (12 if meridiem.lower() == 'pm' else 0)
        if hour_number < 24:
            time_from = f'{hour_number:02d}:{int(minute or 0):02d}'
    return event_date.isoformat(), time_from


def event_description(soup, schema_event):
    article = soup.select_one('article.page-single') or soup.select_one('article')
    sections = article.find_all('section', recursive=False) if article else []
    parts = []
    if len(sections) >= 3:
        parts.append(clean_text(sections[2]))
    summary = clean_text(schema_event.get('description'))
    if summary and not any(summary in part for part in parts):
        parts.insert(0, summary)
    description = clean_text('\n\n'.join(parts))
    return description or None


def city_for_event(schema_event, venue):
    address = (schema_event.get('location') or {}).get('address') or {}
    locality = clean_text(address.get('addressLocality'))
    if locality:
        return locality
    if re.search(r'\bCrawley\b', venue, re.I):
        return 'Crawley'
    if re.search(r"St Mary(?:'s|’s) House", venue, re.I):
        return 'Bramber'
    # BREMF's venue calendar is Brighton based. Touring venues named above are
    # handled explicitly; blank localities in its schema are usually local halls.
    return DEFAULT_CITY


def parse_event(session, url):
    soup = get_soup(session, url)
    schema_event, webpage = schema_nodes(soup)

    title = clean_text(schema_event.get('name')) or clean_text(soup.select_one('article h1'))
    venue = clean_text((schema_event.get('location') or {}).get('name'))
    if not venue:
        venue = labeled_value(soup, 'Venue')
    event_date, time_from = parse_event_datetime(
        visible_date_text(soup), webpage.get('datePublished'), schema_event.get('startDate')
    )
    if not title or not event_date or not venue:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city_for_event(schema_event, venue),
        'country_code': 'GB',
        'description': event_description(soup, schema_event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount(
        'https://',
        HTTPAdapter(
            pool_connections=4,
            pool_maxsize=4,
            max_retries=Retry(
                total=4,
                backoff_factor=1,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=('GET',),
            ),
        ),
    )
    urls = get_event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(parse_event, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class BremfOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bremf_org_uk',
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
    BremfOrgUkCrawler().run()


if __name__ == '__main__':
    main()
