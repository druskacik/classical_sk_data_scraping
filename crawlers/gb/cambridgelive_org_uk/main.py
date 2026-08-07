import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.cambridgelivetickets.co.uk/'
EVENTS_URL = urljoin(SOURCE_URL, 'events')
SOURCE = 'Cambridge Live Tickets'

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
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_urls(session):
    urls = set()
    page = 1
    while True:
        soup = get_soup(session, EVENTS_URL, params={'page': page})
        page_urls = {
            urljoin(SOURCE_URL, link.get('href', '')).split('#', 1)[0]
            for link in soup.select('.listing a[href*="/events/"]')
        }
        page_urls = {url for url in page_urls if url.rstrip('/') != EVENTS_URL}
        new_urls = page_urls - urls
        if not new_urls:
            break
        urls.update(new_urls)

        next_link = soup.select_one(f'a[href*="page={page + 1}"]')
        if not next_link:
            break
        page += 1
    return sorted(urls)


def event_json(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text(strip=True))
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get('@type') == 'Event':
                return item
    return {}


def parse_date(value):
    value = clean_text(value)
    if not value:
        return None
    for pattern in ('%a %d %b %Y', '%d %B %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value[:10] if pattern == '%Y-%m-%d' else value, pattern).date().isoformat()
        except ValueError:
            continue
    match = re.search(r'\d{4}-\d{2}-\d{2}', value)
    if match:
        try:
            return date.fromisoformat(match.group()).isoformat()
        except ValueError:
            pass
    return None


def location_from_json(data):
    locations = data.get('location') or []
    if isinstance(locations, dict):
        locations = [locations]
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get('address') or {}
        venue = clean_text(location.get('name'))
        city = clean_text(address.get('addressLocality')) if isinstance(address, dict) else ''
        if venue and city:
            return venue, city
    return None, None


def performances(soup, data):
    results = []
    for select in soup.select('select.tickets-select-start-time[data-start-date]'):
        event_date = parse_date(select.get('data-start-date'))
        if not event_date:
            continue
        times = []
        for option in select.select('option'):
            match = re.search(r'\b([01]\d|2[0-3]):[0-5]\d\b', clean_text(option.get_text()))
            if match:
                times.append(match.group(0))
        if times:
            results.extend((event_date, event_time) for event_time in times)
        else:
            results.append((event_date, None))

    if results:
        return list(dict.fromkeys(results))

    event_date = parse_date(data.get('startDate'))
    if not event_date:
        date_node = soup.select_one('.event-details__item--date')
        event_date = parse_date(clean_text(date_node))
    if not event_date:
        return []

    event_time = None
    time_node = soup.select_one('.event-details__item--time')
    match = re.search(r'\b([01]\d|2[0-3]):[0-5]\d\b', clean_text(time_node))
    if match:
        event_time = match.group(0)
    return [(event_date, event_time)]


def parse_event(session, url):
    soup = get_soup(session, url)
    data = event_json(soup)
    title = clean_text(data.get('name'))
    if not title:
        heading = soup.select_one('main h1')
        title = clean_text(heading)
    venue, city = location_from_json(data)
    description = clean_text(data.get('description')) or None
    if not title or not venue or not city:
        return []

    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': event_time,
            'venue': venue,
            'city': city,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date, event_time in performances(soup, data)
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(parse_event, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape event detail',
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


class CambridgeLiveOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cambridgelive_org_uk',
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
    CambridgeLiveOrgUkCrawler().run()


if __name__ == '__main__':
    main()
