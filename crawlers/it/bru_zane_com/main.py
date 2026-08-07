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


SOURCE_URL = 'https://bru-zane.com/'
SITEMAP_URL = f'{SOURCE_URL}evento-sitemap.xml'
SOURCE = 'Palazzetto Bru Zane'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'it-IT,it;q=0.9,fr;q=0.8,en;q=0.7',
}

MONTHS = {
    'gennaio': 1, 'febbraio': 2, 'marzo': 3, 'aprile': 4,
    'maggio': 5, 'giugno': 6, 'luglio': 7, 'agosto': 8,
    'settembre': 9, 'ottobre': 10, 'novembre': 11, 'dicembre': 12,
}

# City names are displayed in the site's Italian locale. Events are frequently
# tours, so the country is resolved per performance rather than inherited from
# the institution's home in Venice.
COUNTRY_BY_CITY = {
    'aix-en-provence': 'FR', 'amsterdam': 'NL', 'angoulême': 'FR',
    'barcellona': 'ES', 'berlino': 'DE', 'bologna': 'IT', 'bordeaux': 'FR',
    'bruxelles': 'BE', 'budapest': 'HU', 'caen': 'FR', 'cannes': 'FR',
    'compiègne': 'FR', 'digione': 'FR', 'firenze': 'IT', 'ginevra': 'CH',
    'graz': 'AT', 'grenoble': 'FR', 'lione': 'FR', 'londra': 'GB',
    'losanna': 'CH', 'lucerna': 'CH', 'madrid': 'ES', 'marsiglia': 'FR',
    'metz': 'FR', 'milano': 'IT', 'monaco': 'MC', 'monaco di baviera': 'DE',
    'montpellier': 'FR', 'montréal': 'CA', 'nancy': 'FR', 'nantes': 'FR',
    'new york': 'US', 'nizza': 'FR', 'parigi': 'FR', 'pisa': 'IT',
    'poitiers': 'FR', 'praga': 'CZ', 'reims': 'FR', 'rennes': 'FR',
    'roma': 'IT', 'rouen': 'FR', 'saint-denis': 'FR', 'saint-irénée': 'CA',
    'strasburgo': 'FR', 'tolosa': 'FR', 'torino': 'IT', 'tourcoing': 'FR',
    'tours': 'FR', 'venezia': 'IT', 'versailles': 'FR', 'vienna': 'AT',
    'zurigo': 'CH',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def event_urls(session):
    soup = BeautifulSoup(get(session, SITEMAP_URL).text, 'xml')
    urls = set()
    for location in soup.find_all('loc'):
        url = clean_text(location)
        path = urlparse(url).path.rstrip('/')
        if path.startswith('/evento/') and path != '/evento':
            urls.add(url)
    return sorted(urls)


def parse_datetime(value):
    text = clean_text(value).lower()
    match = re.search(
        r'(\d{1,2})\s+([a-zà-ÿ]+)\s+(\d{4})(?:\s+(\d{1,2})[.:](\d{2}))?',
        text,
    )
    if not match or match.group(2) not in MONTHS:
        return None, None
    try:
        event_date = date(
            int(match.group(3)), MONTHS[match.group(2)], int(match.group(1))
        ).isoformat()
    except ValueError:
        return None, None
    event_time = None
    if match.group(4):
        hour, minute = int(match.group(4)), int(match.group(5))
        if hour < 24 and minute < 60:
            event_time = f'{hour:02d}:{minute:02d}'
    return event_date, event_time


def parse_location(value):
    text = clean_text(value)
    if ',' in text:
        venue, city = (part.strip() for part in text.rsplit(',', 1))
    elif 'palazzetto bru zane' in text.lower():
        venue, city = text, 'Venezia'
    else:
        return None, None, None
    country_code = COUNTRY_BY_CITY.get(city.casefold())
    if not venue or not city or not country_code:
        return None, None, None
    return venue, city, country_code


def description_from(soup):
    parts = []
    for selector in ('.subtitle-event', '.event-descrizione', '.event-program'):
        for node in soup.select(selector):
            value = clean_text(node)
            if value and value not in parts:
                parts.append(value)
    return '\n\n'.join(parts) or None


def parse_event(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title_node = soup.select_one('h1.festival-title') or soup.select_one('h1')
    title = clean_text(title_node)
    if not title:
        return []
    description = description_from(soup)
    records = []
    for performance in soup.select('.program-single-event'):
        event_date, event_time = parse_datetime(performance.select_one('.event-data'))
        venue, city, country_code = parse_location(performance.select_one('.event-venue'))
        if not event_date or not venue or not city or not country_code:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': event_time,
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
    session.mount('https://', HTTPAdapter(max_retries=Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )))
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(get, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_event(future.result().text, url))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Bru Zane event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(records, key=lambda row: (
        row['date'], row['time_from'] or '', row['title'], row['venue']
    ))


class BruZaneComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bru_zane_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='IT',
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
    BruZaneComCrawler().run()


if __name__ == '__main__':
    main()
