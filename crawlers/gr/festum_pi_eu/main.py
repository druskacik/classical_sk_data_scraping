import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.festum-pi.eu/'
SOURCE = 'Festum Pi'
EVENT_LIST_URL = urljoin(SOURCE_URL, 'event-list')
ARCHIVE_FIRST_YEAR = 2022
KNOWN_CITIES = ('Chania', 'Souda', 'Athens', 'Samos', 'Pythagoreio', 'Mytilene')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_text(value):
    if value is None:
        return ''
    text = BeautifulSoup(html.unescape(str(value)), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_page(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.text


def event_links(page_html):
    soup = BeautifulSoup(page_html, 'html.parser')
    links = set()
    for anchor in soup.select('a[href*="/event-details/"]'):
        url = urljoin(SOURCE_URL, anchor.get('href', '')).split('?', 1)[0]
        parsed = urlparse(url)
        if parsed.netloc in {'festum-pi.eu', 'www.festum-pi.eu'} and parsed.path.startswith('/event-details/'):
            links.add(url)
    return links


def discover_event_urls(session):
    # Wix server-renders the event cards. Scan the open listing as well as all
    # published year archives, since past events remain useful to the pipeline.
    pages = [EVENT_LIST_URL]
    pages.extend(urljoin(SOURCE_URL, str(year)) for year in range(ARCHIVE_FIRST_YEAR, date.today().year))
    urls = set()
    for page_url in pages:
        try:
            urls.update(event_links(get_page(session, page_url)))
        except requests.RequestException as error:
            log_message(
                'Failed to inspect Festum Pi event index',
                event='crawler_index_failed',
                level='warning',
                url=page_url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
    return sorted(urls)


def event_json(page_html):
    soup = BeautifulSoup(page_html, 'html.parser')
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and '@graph' in payload:
            candidates = payload.get('@graph', [])
        else:
            candidates = payload
        if isinstance(candidates, dict):
            candidates = [candidates]
        if not isinstance(candidates, list):
            candidates = [payload]
        for candidate in candidates:
            event_type = candidate.get('@type') if isinstance(candidate, dict) else None
            if event_type == 'Event' or isinstance(event_type, list) and 'Event' in event_type:
                return candidate
    return None


def city_from_address(address):
    if isinstance(address, dict):
        city = clean_text(address.get('addressLocality'))
        if city:
            return city
        address = address.get('streetAddress') or address.get('name') or ''
    address = clean_text(address)
    if not address:
        return ''

    for city in KNOWN_CITIES:
        if re.search(rf'\b{re.escape(city)}\b', address, re.IGNORECASE):
            return city

    # Wix commonly emits a single Google-style address, e.g.
    # "Nikiforou Foka 5, Chania 731 32, Greece".
    parts = [part.strip() for part in address.split(',') if part.strip()]
    for part in reversed(parts[:-1] if len(parts) > 1 else parts):
        candidate = re.sub(r'\b\d{3}\s?\d{2}\b.*$', '', part).strip()
        candidate = re.sub(r'\b\d{5}\b.*$', '', candidate).strip()
        if candidate and not re.search(r'\d', candidate):
            return candidate
    return ''


def parse_event(page_html, url):
    data = event_json(page_html)
    if not data:
        return None

    title = clean_text(data.get('name'))
    location = data.get('location') or {}
    if isinstance(location, list):
        location = next((item for item in location if isinstance(item, dict)), {})
    venue = clean_text(location.get('name')) if isinstance(location, dict) else ''
    address = location.get('address') if isinstance(location, dict) else ''
    location_text = f'{clean_text(address)} {venue}'
    city = next(
        (city for city in KNOWN_CITIES if re.search(rf'\b{re.escape(city)}\b', location_text, re.IGNORECASE)),
        '',
    )
    city = city or city_from_address(address) or city_from_address(venue)
    if venue.casefold() == city.casefold():
        address_text = clean_text(address)
        # Some Wix records put the city in `name` and the actual auditorium in
        # `address`. A street address is not a defensible venue, so omit it.
        venue = address_text if address_text and not re.search(r'\d', address_text) else ''

    start_value = clean_text(data.get('startDate'))
    try:
        start = datetime.fromisoformat(start_value.replace('Z', '+00:00'))
        event_date = start.date().isoformat()
        time_from = start.strftime('%H:%M')
    except ValueError:
        return None

    if not title or not venue or not city:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'GR',
        'description': clean_text(data.get('description')) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = discover_event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_page, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_event(future.result(), url)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Festum Pi event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class FestumPiEuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='festum_pi_eu',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GR',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    FestumPiEuCrawler().run()


if __name__ == '__main__':
    main()
