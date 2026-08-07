import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.brb.org.uk/'
SOURCE = 'Birmingham Royal Ballet'
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

VENUE_CITIES = {
    'alexandra theatre': 'Birmingham',
    'birmingham hippodrome': 'Birmingham',
    'birmingham repertory theatre': 'Birmingham',
    'brb studios': 'Birmingham',
    'crescent theatre': 'Birmingham',
    'royal albert hall': 'London',
    "sadler's wells": 'London',
    'sympony hall': 'Birmingham',
    'symphony hall': 'Birmingham',
    'theatre royal plymouth': 'Plymouth',
    'lowry': 'Salford',
    'mayflower theatre': 'Southampton',
    'sunderland empire': 'Sunderland',
    'theatre royal nottingham': 'Nottingham',
    'theatre royal, nottingham': 'Nottingham',
    'royal opera house': 'London',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def canonical_url(url):
    parts = urlsplit(urljoin(SOURCE_URL, url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def show_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    return sorted({
        canonical_url(node.get_text(strip=True))
        for node in soup.select('url > loc')
        if '/shows/' in urlsplit(node.get_text(strip=True)).path
    })


def parse_json_script(node):
    if not node:
        return {}
    try:
        return json.loads(node.get_text())
    except (json.JSONDecodeError, TypeError):
        # Some archived descriptions contain unescaped control characters.
        match = re.search(
            r'"startDate"\s*:\s*"([^"]+)"', node.get_text(), re.S
        )
        return {'startDate': match.group(1)} if match else {}


def resolve_city(venue):
    folded = venue.casefold()
    for venue_name, city in VENUE_CITIES.items():
        if venue_name in folded:
            return city
    return None


def page_description(soup):
    parts = []
    for section in soup.select('main section.c-event__section'):
        if section.get('id') == 'dates-and-times':
            continue
        text = clean_text(section)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def detail_records(session, url):
    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
    title = clean_text(soup.select_one('h1.c-event__header__title'))
    description = page_description(soup)
    records = []

    for instance in soup.select('v-instance'):
        data = parse_json_script(instance.select_one('script[type="application/ld+json"]'))
        value = data.get('startDate')
        venue_node = instance.select_one('[venue-title]')
        venue = clean_text(venue_node.get('venue-title')) if venue_node else ''
        city = resolve_city(venue)
        if not title or not value or not venue or not city:
            continue
        try:
            start = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (TypeError, ValueError):
            continue
        records.append({
            'title': title,
            'date': start.date().isoformat(),
            'url': url,
            'time_from': start.strftime('%H:%M'),
            'venue': venue,
            'city': city,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = show_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(detail_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape BRB show detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'],
            record['venue'], record['url'],
        ),
    )


class BrbOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='brb_org_uk',
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
    BrbOrgUkCrawler().run()


if __name__ == '__main__':
    main()
