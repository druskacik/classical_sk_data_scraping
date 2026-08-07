import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.barbican.org.uk/'
SOURCE = 'Barbican'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on')
CITY = 'London'
TIMEZONE = ZoneInfo('Europe/London')
VENUE_NAMES = {
    'Hall': 'Barbican Hall',
    'St Giles': "St Giles' Cripplegate",
    'St Giles Cripplegate': "St Giles' Cripplegate",
    'LSO St Luke’s': "LSO St Luke's",
}

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
    return BeautifulSoup(response.content, 'html.parser')


def listing_urls(session):
    # The public filter value for Classical music is 6. Pages are rendered on
    # the server; no catalogue API is called by the browser.
    urls = set()
    page = 0
    while True:
        soup = get_soup(
            session,
            LISTING_URL,
            params={'af[6]': '6', 'page': page} if page else {'af[6]': '6'},
        )
        page_urls = {
            urljoin(SOURCE_URL, link.get('href'))
            for link in soup.select('article a[href*="/event/"]')
            if link.get('href')
        }
        new_urls = page_urls - urls
        urls.update(page_urls)
        if not new_urls or not soup.select_one('.pager a[href]'):
            break
        page += 1
    return urls


def parse_start(time_element):
    value = time_element.get('datetime') if time_element else None
    if not value:
        return None
    try:
        start = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if start.tzinfo is not None:
            start = start.astimezone(TIMEZONE)
        return start
    except ValueError:
        return None


def detail_records(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.select_one('h1.heading-group__primary'))
    venue = clean_text(soup.select_one('.event-byline__venue')).strip(' ,')
    venue = VENUE_NAMES.get(venue, venue)
    byline = soup.select_one('.event-byline__date')
    starts = []
    for time_element in byline.select('time[datetime]') if byline else []:
        start = parse_start(time_element)
        if start:
            starts.append(start)

    content = soup.select_one('.event-content__layout-container')
    if content:
        for element in content.select(
            'script, style, .layout-main-with-sidebar__sidebar, .trimmed-content__button'
        ):
            element.decompose()
    description = clean_text(content) or None

    if not title or not venue or not starts:
        return []

    records = []
    seen = set()
    for start in starts:
        identity = (start.date(), start.hour, start.minute)
        if identity in seen:
            continue
        seen.add(identity)
        records.append({
            'title': title,
            'date': start.date().isoformat(),
            'url': url,
            'time_from': start.strftime('%H:%M'),
            'venue': venue,
            'city': CITY,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(detail_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Barbican event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda item: (item['date'], item['time_from'] or '', item['title']),
    )


class BarbicanOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='barbican_org_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
    BarbicanOrgUkCrawler().run()


if __name__ == '__main__':
    main()
