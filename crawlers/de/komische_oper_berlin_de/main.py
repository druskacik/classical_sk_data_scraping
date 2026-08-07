import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.komische-oper-berlin.de/'
CALENDAR_URL = urljoin(SOURCE_URL, 'spielplan/kalender/')
SOURCE = 'Komische Oper Berlin'
CITY = 'Berlin'

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
    text = text.replace('\xad', '').replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def previous_month_url(value):
    year = value.year if value.month > 1 else value.year - 1
    month = value.month - 1 if value.month > 1 else 12
    return f'{CALENDAR_URL}{year:04d}-{month:02d}/'


def calendar_urls(session):
    # The month navigation contains a whole season. Probing the preceding
    # month also exposes the previous season at the August season boundary,
    # including performances which remain in the public archive.
    soups = [get_soup(session, CALENDAR_URL)]
    try:
        soups.append(get_soup(session, previous_month_url(date.today())))
    except requests.RequestException as error:
        log_message(
            'Failed to inspect previous calendar season',
            event='crawler_archive_failed',
            level='warning',
            url=previous_month_url(date.today()),
            error_type=type(error).__name__,
            error_message=str(error),
        )

    urls = set()
    pattern = re.compile(r'/spielplan/kalender/\d{4}-\d{2}/?$')
    for soup in soups:
        for link in soup.select('a[href]'):
            url = urljoin(SOURCE_URL, link.get('href'))
            if pattern.search(urlparse(url).path):
                urls.add(url.split('?', 1)[0])
    return sorted(urls)


def listing_items(soup):
    items = []
    for element in soup.select('.performance.js-schedule-element'):
        title_node = element.select_one('.performance__title [itemprop="name"]')
        link = element.select_one('a.performance__link[href]')
        start = element.select_one('[itemprop="startDate"][content]')
        venue_node = element.select_one('.performance__location')
        title = clean_text(title_node)
        venue = clean_text(venue_node)
        url = urljoin(SOURCE_URL, link.get('href')) if link else ''
        start_value = start.get('content', '') if start else ''
        match = re.fullmatch(r'(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::\d{2})?', start_value)
        if not title or not url or not venue or not match:
            continue
        try:
            event_date = date.fromisoformat(match.group(1)).isoformat()
        except ValueError:
            continue
        items.append(
            {
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': f'{match.group(2)}:{match.group(3)}',
                'venue': venue,
            }
        )
    return items


def detail_description(session, url):
    soup = get_soup(session, url)
    parts = []
    for node in soup.select('.richtext__text'):
        text = clean_text(node)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def make_record(item, description):
    return {
        **item,
        'city': CITY,
        'country_code': 'DE',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    items_by_key = {}
    for url in calendar_urls(session):
        try:
            for item in listing_items(get_soup(session, url)):
                key = (item['title'], item['date'], item['time_from'], item['venue'])
                items_by_key[key] = item
        except requests.RequestException as error:
            log_message(
                'Failed to scrape calendar month',
                event='crawler_page_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )

    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(detail_description, session, item['url']): item
            for item in items_by_key.values()
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                description = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                description = None
            records.append(make_record(item, description))

    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'] or '', record['title'], record['url']),
    )


class KomischeOperBerlinDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='komische_oper_berlin_de',
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
    KomischeOperBerlinDeCrawler().run()


if __name__ == '__main__':
    main()
