import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://nps.ba/'
REPERTOIRE_URL = urljoin(SOURCE_URL, 'repertoar')
SOURCE = 'Narodno pozorište Sarajevo'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'bs-BA,bs;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def repertoire_urls(session):
    page_url = REPERTOIRE_URL
    seen_pages = set()
    event_urls = set()
    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        soup = get_soup(session, page_url)
        for link in soup.select('a[href*="/repertoar/"]'):
            url = urljoin(SOURCE_URL, link.get('href', ''))
            path = urlparse(url).path
            if re.fullmatch(r'/repertoar/[^/]+/\d+', path):
                event_urls.add(url)
        next_link = soup.select_one('a[rel="next"]')
        page_url = urljoin(SOURCE_URL, next_link['href']) if next_link else None
    return sorted(event_urls)


def event_json_ld(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get('@type') == 'Event':
                return item
    return {}


def description_text(soup):
    parts = []
    for selector in ('div.eft', 'div.eft-special'):
        node = soup.select_one(selector)
        if not node:
            continue
        # Navigation controls in the synopsis container are not event prose.
        for unwanted in node.select('button, a, svg, #learn_more'):
            unwanted.decompose()
        text = clean_text(node)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_event_page(soup, url):
    data = event_json_ld(soup)
    title = clean_text(data.get('name'))
    location = data.get('location') or {}
    address = location.get('address') or {}
    venue = clean_text(location.get('name'))
    city = clean_text(address.get('addressLocality'))
    country_code = clean_text(address.get('addressCountry')).upper()

    if not title:
        heading = soup.select_one('h1')
        title = clean_text(next(heading.strings, '')) if heading else ''
    if not venue or not city:
        return []
    if not re.fullmatch(r'[A-Z]{2}', country_code):
        country_code = 'BA'

    description = description_text(soup)
    records = []
    seen_starts = set()
    calendar_heading = next(
        (heading for heading in soup.select('h2') if 'kalendar izvedbi' in clean_text(heading).lower()),
        None,
    )
    calendar = calendar_heading.find_parent('div', class_='bg-white') if calendar_heading else None
    for time_node in calendar.select('time[datetime]') if calendar else []:
        raw_start = time_node.get('datetime', '').strip()
        try:
            start = datetime.strptime(raw_start, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        start_key = start.isoformat(timespec='minutes')
        if start_key in seen_starts:
            continue
        seen_starts.add(start_key)
        records.append({
            'title': title,
            'date': start.date().isoformat(),
            'url': url,
            'time_from': start.strftime('%H:%M'),
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records if title else []


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = repertoire_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_event_page(future.result(), url))
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape repertoire detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    records = sorted(records, key=lambda record: (
        record['date'], record['time_from'] or '', record['title'], record['url']
    ))
    unique = {}
    for record in records:
        key = (record['title'], record['date'], record['time_from'], record['venue'])
        unique.setdefault(key, record)
    return list(unique.values())


class NpsBaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='nps_ba',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='BA',
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
    NpsBaCrawler().run()


if __name__ == '__main__':
    main()
