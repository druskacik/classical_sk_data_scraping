import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.amare.nl/'
AGENDA_URL = urljoin(SOURCE_URL, 'nl/agenda')
SOURCE = 'Amare'
DEFAULT_CITY = 'Den Haag'
DEFAULT_VENUE = 'Amare'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'nl-NL,nl;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def event_links(session):
    links = set()
    page = 1
    last_page = 1
    while page <= last_page:
        soup = get_soup(session, AGENDA_URL, params={'page': page})
        page_select = soup.select_one('#pagination-select')
        if page_select:
            values = [int(option.get('value')) for option in page_select.select('option')]
            last_page = max(values, default=last_page)

        for anchor in soup.select('main a[href]'):
            url = urljoin(SOURCE_URL, anchor.get('href'))
            path = urlparse(url).path
            if re.fullmatch(r'/(?:nl/)?agenda/[^/]+', path.rstrip('/')):
                links.add(url)
        page += 1
    return sorted(links)


def schema_events(soup):
    events = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get('@type') == 'Event':
                events.append(item)
    return events


def detail_description(soup, event):
    parts = []
    selectors = (
        '.teaserWrapper .richtext',
        '.programmeWrapper .richtext',
        '.desc1Wrapper .richtext',
    )
    for selector in selectors:
        for node in soup.select(selector):
            text = clean_text(node)
            if text and text not in parts:
                if 'programmeWrapper' in selector:
                    text = f'Programma\n{text}'
                parts.append(text)
    fallback = clean_text(event.get('description'))
    if fallback and not parts:
        parts.append(fallback)
    return '\n\n'.join(parts) or None


def resolve_location(event):
    location = event.get('location') or {}
    if not isinstance(location, dict):
        return None, None
    address = location.get('address') or {}
    if not isinstance(address, dict):
        address = {}
    venue = clean_text(location.get('name'))
    city = clean_text(address.get('addressLocality'))

    # Amare's own calendar is based in Den Haag. Its schema names every
    # outside hall, so defaults are only used when no contrary location is given.
    if not venue and not city:
        return DEFAULT_VENUE, DEFAULT_CITY
    if venue and not city:
        location_text = venue.lower()
        if any(name in location_text for name in ('amare', 'nieuwe kerk', 'koninklijk conservatorium')):
            city = DEFAULT_CITY
    if city and not venue and city.lower() in ('den haag', 'the hague', "'s-gravenhage"):
        venue = DEFAULT_VENUE
    return venue or None, city or None


def make_record(event, url, description):
    title = clean_text(event.get('name'))
    start = event.get('startDate') or ''
    try:
        parsed = datetime.fromisoformat(start.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    venue, city = resolve_location(event)
    if not title or not venue or not city:
        return None
    return {
        'title': title,
        'date': parsed.date().isoformat(),
        'url': url,
        'time_from': parsed.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'NL',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def scrape_detail(session, url):
    soup = get_soup(session, url)
    events = schema_events(soup)
    return [
        record
        for event in events
        if (record := make_record(event, url, detail_description(soup, event)))
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    links = event_links(session)
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(scrape_detail, session, url): url for url in links}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'], record['title'], record['url']),
    )


class AmareNlCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='amare_nl',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='NL',
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
    AmareNlCrawler().run()


if __name__ == '__main__':
    main()
