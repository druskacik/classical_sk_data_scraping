import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.sydneyoperahouse.com/'
SOURCE = 'Sydney Opera House'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'en-AU,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_html(session, url, params=None):
    response = session.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response.text


def listing_urls(session):
    # Supplying a wide range exposes anything the public calendar still retains,
    # including productions whose run has started, and is not limited to its
    # default rolling twelve-month window.
    params = {
        'date_range[min]': '2000-01-01',
        'date_range[max]': '2100-12-31',
    }
    page = 0
    urls = set()
    while True:
        page_params = {**params, 'page': page}
        soup = BeautifulSoup(get_html(session, LISTING_URL, page_params), 'html.parser')
        cards = soup.select('.views-view-responsive-grid__item')
        for card in cards:
            link = card.select_one('h3 a[href]')
            if link:
                urls.add(urljoin(SOURCE_URL, link['href']))

        next_link = soup.select_one('.pager a[rel="next"]')
        if not cards or next_link is None:
            break
        page += 1

    if not urls:
        raise ValueError('The public calendar returned no event URLs')
    return sorted(urls)


def event_schema(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or '')
        except (TypeError, json.JSONDecodeError):
            continue
        nodes = payload.get('@graph', []) if isinstance(payload, dict) else []
        for node in nodes:
            if isinstance(node, dict) and node.get('@type') == 'Event':
                return node
    return {}


def parse_performance(value):
    text = clean_text(value).replace(',', '')
    match = re.search(
        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+'
        r'(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+(\d{1,2}(?::\d{2})?\s*[ap]m)\b',
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    try:
        start = datetime.strptime(f'{match.group(1)} {match.group(2).replace(" ", "")}', '%d %B %Y %I:%M%p')
    except ValueError:
        try:
            start = datetime.strptime(f'{match.group(1)} {match.group(2).replace(" ", "")}', '%d %B %Y %I%p')
        except ValueError:
            return None
    return start.date().isoformat(), start.strftime('%H:%M')


def description(soup):
    parts = []
    selectors = (
        '.event-header__col--main .tagline',
        '.event-header__col--main .expander__content',
        '.event__content',
    )
    for selector in selectors:
        for element in soup.select(selector):
            text = clean_text(element)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def parse_event(session, url):
    soup = BeautifulSoup(get_html(session, url), 'html.parser')
    schema = event_schema(soup)
    title = clean_text(schema.get('name')) or clean_text(soup.select_one('h1'))
    location = schema.get('location') or {}
    address = location.get('address') or {}
    venue = clean_text(location.get('name'))
    city = clean_text(address.get('addressLocality'))
    country_code = clean_text(address.get('addressCountry')).upper()
    if not all((title, venue, city)) or country_code != 'AU':
        return []

    performances = []
    for element in soup.select('.performance'):
        parsed = parse_performance(element)
        if parsed and parsed not in performances:
            performances.append(parsed)

    if not performances:
        try:
            start = datetime.fromisoformat(schema.get('startDate'))
            performances.append((start.date().isoformat(), start.strftime('%H:%M')))
        except (TypeError, ValueError):
            return []

    event_description = description(soup)
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': event_description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date, time_from in performances
    ]


class SydneyOperaHouseComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='sydneyoperahouse_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
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
                        'Failed to scrape concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['venue']
            ),
        )


def main():
    SydneyOperaHouseComCrawler().run()


if __name__ == '__main__':
    main()
