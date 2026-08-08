import html
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.sydneyphilharmonia.com.au/'
EVENTS_URL = urljoin(SOURCE_URL, 'events/')
EVENTS_API = urljoin(SOURCE_URL, 'wp-json/tribe/events/v1/events')
SOURCE = 'Sydney Philharmonia Choirs'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

GB_CITIES = {'Ely', 'Gloucester', 'London', 'Oxford'}
KNOWN_VENUE_CITIES = {
    "All Saints Cathedral, Bathurst": 'Bathurst',
    'Gloucester Cathedral': 'Gloucester',
    "St Andrew’s Cathedral": 'Sydney',
    "St Philip’s Church, York Street, Sydney": 'Sydney',
    "St Peter and Paul’s Old Cathedral, Goulburn": 'Goulburn',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(html.unescape(str(value)), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def canonical_url(url):
    parts = urlsplit(urljoin(SOURCE_URL, url or ''))
    return urlunsplit(('https', 'www.sydneyphilharmonia.com.au', parts.path, '', ''))


def get(session, url, params=None):
    response = session.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response


def fetch_all_events(session):
    events = []
    page = 1
    while True:
        payload = get(
            session,
            EVENTS_API,
            params={
                'start_date': '2000-01-01',
                'per_page': 50,
                'page': page,
            },
        ).json()
        events.extend(payload.get('events') or [])
        if page >= int(payload.get('total_pages') or 1):
            return events
        page += 1


def detail_urls(session):
    soup = BeautifulSoup(get(session, EVENTS_URL).text, 'html.parser')
    urls = set()
    for link in soup.select('a[href]'):
        url = canonical_url(link.get('href'))
        path = urlsplit(url).path.rstrip('/')
        if path.startswith('/events/') and path != '/events':
            urls.add(url)
    return sorted(urls)


def detail_description(session, url):
    soup = BeautifulSoup(get(session, url).text, 'html.parser')
    main = soup.select_one('main')
    if not main:
        return None
    text = clean_text(main.get_text('\n', strip=True))
    # Admission information and the global footer are not concert descriptions.
    text = re.split(
        r'\n(?:TICKETS|BOOK TICKETS|BOOK NOW|Partnered with|Social media)\s*\n',
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    return text or None


def normalized_words(value):
    value = unicodedata.normalize('NFKD', clean_text(value)).encode('ascii', 'ignore').decode()
    return set(re.findall(r'[a-z0-9]+', value.lower()))


def detail_key(url):
    return urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]


def best_description(event, descriptions):
    event_words = normalized_words(event.get('title'))
    best_score = 0
    best_text = None
    for url, text in descriptions.items():
        slug_words = normalized_words(detail_key(url))
        if not slug_words:
            continue
        score = len(event_words & slug_words) / len(slug_words)
        if score > best_score:
            best_score, best_text = score, text
    if best_score >= 0.5:
        return best_text
    fallback = clean_text(event.get('description') or event.get('excerpt'))
    return fallback or None


def resolve_location(event):
    venue_data = event.get('venue') or {}
    venue = clean_text(venue_data.get('venue'))
    city = clean_text(venue_data.get('city'))
    # This performance's API venue is empty, but its linked detail page names
    # the Concert Hall explicitly.
    title_words = normalized_words(event.get('title'))
    if not venue and {'bachs', 'st', 'john', 'passion'}.issubset(title_words):
        venue, city = 'Sydney Opera House Concert Hall', 'Sydney'
    if not city:
        city = KNOWN_VENUE_CITIES.get(venue, '')
    if not venue or not city:
        return None, None, None

    country = clean_text(venue_data.get('country')).lower()
    country_code = 'GB' if city in GB_CITIES or country in {
        'england', 'great britain', 'united kingdom', 'uk'
    } else 'AU'
    return venue, city, country_code


def make_record(event, descriptions):
    title = clean_text(event.get('title'))
    url = canonical_url(event.get('url'))
    venue, city, country_code = resolve_location(event)
    try:
        start = datetime.strptime(event.get('start_date') or '', '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None
    if not title or not url or not venue or not city or not country_code:
        return None
    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': url,
        'time_from': None if event.get('all_day') else start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': best_description(event, descriptions),
    }


def scrape_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = fetch_all_events(session)

    descriptions = {}
    urls = detail_urls(session)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(detail_description, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                descriptions[url] = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape Sydney Philharmonia event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    records = [make_record(event, descriptions) for event in events]
    records = [record for record in records if record]
    return sorted(
        records,
        key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['venue']
        ),
    )


class SydneyPhilharmoniaComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='sydneyphilharmonia_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        return scrape_concerts()


def main():
    SydneyPhilharmoniaComAuCrawler().run()


if __name__ == '__main__':
    main()
