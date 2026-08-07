import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://bachconsort.org/'
EVENTS_API = 'https://bachconsort.org/wp-json/wp/v2/events'
SOURCE = 'Washington Bach Consort'

HEADERS = {
    'Accept': 'text/html,application/json',
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
}

VENUE_CITIES = {
    'National Presbyterian Church': 'Washington',
    'Live! at 10th & G': 'Washington',
    'First Congregational United Church of Christ': 'Washington',
    'Church of the Epiphany': 'Washington',
    'St. Mark’s Capitol Hill': 'Washington',
    "St. Mark's Capitol Hill": 'Washington',
    'St. Paul’s Lutheran Church': 'Washington',
    "St. Paul's Lutheran Church": 'Washington',
    'The Parks at Walter Reed': 'Washington',
    'The Parks at Walter Reeds': 'Washington',
    'St. Paul’s Episcopal Church': 'Alexandria',
    "St. Paul's Episcopal Church": 'Alexandria',
    'Virginia Theological Seminary': 'Alexandria',
    'St. George’s Episcopal Church': 'Arlington',
    "St. George's Episcopal Church": 'Arlington',
    'The Falls Church Episcopal': 'Falls Church',
    'Music Center at Strathmore': 'North Bethesda',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_event_urls(session):
    urls = []
    page = 1
    while True:
        response = session.get(
            EVENTS_API,
            params={'per_page': 100, 'page': page, '_fields': 'link'},
            timeout=45,
        )
        response.raise_for_status()
        urls.extend(item.get('link') for item in response.json() if item.get('link'))
        total_pages = int(response.headers.get('X-WP-TotalPages', '1'))
        if page >= total_pages:
            break
        page += 1
    return urls


def event_description(soup):
    content = soup.select_one('.wbc-accordion__content')
    if not content:
        return None
    preview = content.select_one('.wbc-accordion__content__preview')
    if preview:
        preview.decompose()
    return clean_text(content.get_text('\n', strip=True)) or None


def make_records(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title_tag = soup.select_one('main h1.entry-title')
    title = clean_text(title_tag.get_text(' ', strip=True) if title_tag else '')
    performance_blocks = soup.select('.wbc-event-info__details .wbc-dates-mobile p')
    if not title or not performance_blocks:
        return []

    performances = []
    for block in performance_blocks:
        time_tag = block.find('time', datetime=True)
        venue_tag = block.select_one('a.block')
        venue = clean_text(venue_tag.get_text(' ', strip=True) if venue_tag else '')
        city = VENUE_CITIES.get(venue)
        if not time_tag or not venue or not city:
            continue
        try:
            start = datetime.fromisoformat(time_tag['datetime'])
        except (TypeError, ValueError):
            continue
        performances.append((start, venue, city))

    description = event_description(soup)
    records = []
    for start, venue, city in performances:
        records.append(
            {
                'title': title,
                'date': start.date().isoformat(),
                'url': url,
                'time_from': start.strftime('%H:%M'),
                'venue': venue,
                'city': city,
                'country_code': 'US',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )
    return records


class BachConsortOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bachconsort_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
        upload_target='classical',
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
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            urls = get_event_urls(session)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch event index',
                event='crawler_fetch_failed',
                level='error',
                url=EVENTS_API,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        records = []
        for url in urls:
            try:
                response = session.get(url, timeout=45)
                response.raise_for_status()
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch event page',
                    event='crawler_event_fetch_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            records.extend(make_records(response.text, url))

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['venue']
            ),
        )


def main():
    BachConsortOrgCrawler().run()


if __name__ == '__main__':
    main()
