import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.altemusik.at/de'
PROGRAM_URL = 'https://www.altemusik.at/de/veranstaltungen'
API_URL = 'https://www.altemusik.at/dynamic-search/fewo_schedule/j-schedule'
SOURCE = 'Innsbrucker Festwochen der Alten Musik'
TIMEZONE = ZoneInfo('Europe/Vienna')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
        respect_retry_after_header=True,
    )
    session.mount('https://', HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8))
    return session


def api_params(page):
    return {
        'sections': '',
        'subscriptions': '',
        'attributes': '',
        'page': page,
        'search': '',
        'startDate': '',
        'endDate': '',
        'premiere': 0,
        'freeentry': 0,
        # This exposes dates earlier in the current season as well as future ones.
        'showPastActivities': 1,
        'ages': '',
        'stages': '',
        'configId': 16400,
        'preselectConfigId': 0,
        'useDate': 0,
        'freundeskreis': 0,
        'locale': 'de',
    }


def fetch_api_page(session, page):
    response = session.get(API_URL, params=api_params(page), timeout=45)
    response.raise_for_status()
    data = response.json().get('activitiesData') or {}
    activities = data.get('activities') or {}
    if isinstance(activities, dict):
        items = [item for group in activities.values() for item in group]
    else:
        items = []
    return data, items


def fetch_catalog(session):
    first_data, first_items = fetch_api_page(session, 1)
    total = int(first_data.get('total_count') or len(first_items))
    per_page = int(first_data.get('per_page') or max(len(first_items), 1))
    page_count = max(1, math.ceil(total / per_page))
    pages = {1: first_items}

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_api_page, session, page): page
            for page in range(2, page_count + 1)
        }
        for future in as_completed(futures):
            page = futures[future]
            try:
                pages[page] = future.result()[1]
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to fetch Alte Musik programme page',
                    event='crawler_page_failed',
                    level='warning',
                    url=API_URL,
                    page=page,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    missing = sorted(set(range(1, page_count + 1)) - pages.keys())
    if missing:
        raise requests.RequestException(
            f'Alte Musik programme incomplete; missing {len(missing)} API pages'
        )
    return [item for page in sorted(pages) for item in pages[page]]


def fetch_description(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    parts = []
    for heading in soup.select('.component-container__headline'):
        if clean_text(heading).lower() == 'werke von':
            container = heading.find_parent(class_='component-container')
            text = clean_text(container)
            if text:
                parts.append(text)
    for selector in (
        '.production__header-description',
        '#production-program',
    ):
        for node in soup.select(selector):
            text = clean_text(node)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def fetch_descriptions(session, urls):
    descriptions = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_description, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                descriptions[url] = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to fetch Alte Musik event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return descriptions


def city_for_venue(venue):
    if venue.startswith('Alpengasthof St. Magdalena'):
        return 'Absam'
    if venue.startswith('Stift Stams'):
        return 'Stams'
    return 'Innsbruck'


def get_concerts():
    session = make_session()
    activities = fetch_catalog(session)
    urls = {
        urljoin(SOURCE_URL + '/', item.get('production_link', ''))
        for item in activities
        if item.get('production_link')
    }
    descriptions = fetch_descriptions(session, urls)

    records = []
    for item in activities:
        title = clean_text(item.get('title'))
        venue = clean_text(item.get('stage'))
        link = item.get('production_link')
        timestamp = item.get('start')
        if not all((title, venue, link, timestamp)):
            continue
        try:
            starts_at = datetime.fromtimestamp(int(timestamp), TIMEZONE)
        except (TypeError, ValueError, OverflowError):
            continue
        url = urljoin(SOURCE_URL + '/', link)
        teaser = clean_text(item.get('description')) or None
        records.append({
            'title': title,
            'date': starts_at.date().isoformat(),
            'url': url,
            'time_from': starts_at.strftime('%H:%M') if item.get('publishStartTime', True) else None,
            'venue': venue,
            'city': city_for_venue(venue),
            'country_code': 'AT',
            'description': descriptions.get(url) or teaser,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return sorted(records, key=lambda record: (
        record['date'], record['time_from'] or '', record['title'], record['venue']
    ))


class AlteMusikAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='altemusik_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
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
    AlteMusikAtCrawler().run()


if __name__ == '__main__':
    main()
