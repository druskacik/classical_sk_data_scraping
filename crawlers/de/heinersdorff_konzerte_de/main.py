import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.heinersdorff-konzerte.de/de'
CONCERTS_URL = f'{SOURCE_URL}/konzerte'
CALENDAR_URL = f'{SOURCE_URL}/api/productions/calendar/'
SOURCE = 'Heinersdorff Konzerte'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response


def listing_urls(session):
    """Return all productions reachable through the site's published calendar.

    The listing renders ten concerts beginning at its ``date`` query value.
    Asking for every date exposed by the calendar API also reaches archived
    concerts, rather than only the ten concerts rendered on the default page.
    """
    dates = get_response(session, CALENDAR_URL).json()
    query_dates = [value for value in dates if re.fullmatch(r'\d{2}\.\d{2}\.\d{4}', str(value))]
    if not query_dates:
        query_dates = [None]

    urls = set()
    for query_date in query_dates:
        params = {'date': query_date} if query_date else None
        soup = BeautifulSoup(get_response(session, CONCERTS_URL, params=params).text, 'html.parser')
        for link in soup.select('a[href*="/de/konzerte/"]'):
            href = link.get('href', '')
            if re.search(r'/de/konzerte/[^/]+/\d+/?$', href):
                urls.add(urljoin(SOURCE_URL, href))
    return sorted(urls)


def event_schema(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get('@type') == 'MusicEvent':
                return item
    return {}


def event_description(soup):
    parts = []
    for selector in ('.event-schedule', '.event-description'):
        text = clean_text(soup.select_one(selector))
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_event(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    schema = event_schema(soup)
    title = clean_text(soup.select_one('h1.event-hero__headline')) or clean_text(schema.get('name'))
    start = str(schema.get('startDate') or '')
    start_match = re.match(r'(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2}))?', start)

    location = schema.get('location') if isinstance(schema.get('location'), dict) else {}
    address = location.get('address') if isinstance(location.get('address'), dict) else {}
    city = clean_text(address.get('addressLocality'))
    venue = clean_text(soup.select_one('.event-hero__venue')) or clean_text(location.get('name'))
    if not title or not start_match or not venue or not city:
        return None
    try:
        event_date = date.fromisoformat(start_match.group(1)).isoformat()
    except ValueError:
        return None

    time_from = None
    if start_match.group(2) and start_match.group(3):
        time_from = f'{start_match.group(2)}:{start_match.group(3)}'

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'DE',
        'description': event_description(soup),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_event(future.result().text, url)
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape concert detail',
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
        key=lambda record: (record['date'], record['time_from'] or '', record['title'], record['url']),
    )


class HeinersdorffKonzerteDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='heinersdorff_konzerte_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
    HeinersdorffKonzerteDeCrawler().run()


if __name__ == '__main__':
    main()
