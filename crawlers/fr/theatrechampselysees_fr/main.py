import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.theatrechampselysees.fr/'
SOURCE = 'Théâtre des Champs-Élysées'
CALENDAR_URL = urljoin(SOURCE_URL, 'calendrier')
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')
VENUE = 'Théâtre des Champs-Élysées'
CITY = 'Paris'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9',
}
AJAX_HEADERS = {**HEADERS, 'X-Requested-With': 'XMLHttpRequest'}

MONTHS = {
    'janvier': 1,
    'février': 2,
    'mars': 3,
    'avril': 4,
    'mai': 5,
    'juin': 6,
    'juillet': 7,
    'août': 8,
    'septembre': 9,
    'octobre': 10,
    'novembre': 11,
    'décembre': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url, params=None, headers=None):
    response = session.get(url, params=params, headers=headers, timeout=45)
    response.raise_for_status()
    return response


def sitemap_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    urls = {clean_text(node) for node in soup.select('url > loc')}
    return {
        url for url in urls
        if re.search(r'^https://www\.theatrechampselysees\.fr/saison-\d{4}-\d{2,4}/', url)
    }


def calendar_urls(session):
    first = BeautifulSoup(
        get_response(session, CALENDAR_URL).content,
        'html.parser',
    )
    slugs = {node.get('data-slug') for node in first.select('[data-slug]')}
    slugs = {slug for slug in slugs if re.fullmatch(r'\d{2}-\d{2}', slug or '')}
    urls = set()

    def load_month(slug):
        response = get_response(
            session, CALENDAR_URL, params={'slug': slug}, headers=AJAX_HEADERS
        )
        return BeautifulSoup(response.content, 'html.parser')

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(load_month, slug): slug for slug in slugs}
        for future in as_completed(futures):
            slug = futures[future]
            try:
                soup = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape TCE calendar month',
                    event='crawler_item_failed',
                    level='warning',
                    url=f'{CALENDAR_URL}?slug={slug}',
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            for link in soup.select('.calendar__event a[href]'):
                urls.add(urljoin(SOURCE_URL, link.get('href')))
    return urls


def parse_performance(value):
    text = clean_text(value).casefold()
    match = re.search(
        r'\b(\d{1,2})\s+('
        + '|'.join(MONTHS)
        + r')\s+(\d{4})(?:\s*-?\s*(\d{1,2})h(\d{2}))?',
        text,
    )
    if not match:
        return None
    try:
        event_date = date(
            int(match.group(3)), MONTHS[match.group(2)], int(match.group(1))
        ).isoformat()
    except ValueError:
        return None
    time_from = None
    if match.group(4):
        hour, minute = int(match.group(4)), int(match.group(5))
        if hour > 23 or minute > 59:
            return None
        time_from = f'{hour:02d}:{minute:02d}'
    return event_date, time_from


def page_description(soup):
    parts = []
    for selector in (
        '.event-detail__title h2',
        '.event-detail__description',
        '.event-detail__content',
    ):
        for element in soup.select(selector):
            text = clean_text(element)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def detail_records(session, url):
    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
    title_node = soup.select_one('.event-detail__title h1') or soup.find('h1')
    title = clean_text(title_node)
    if not title:
        return []

    # The mobile and desktop widgets repeat identical dates. Read one date list
    # and de-duplicate again for templates that do not use the usual wrapper.
    date_list = soup.select_one('.event-detail__dates .widget__dates')
    nodes = date_list.select('.widget__dates-item') if date_list else soup.select(
        '.widget__dates-item'
    )
    performances = {value for node in nodes if (value := parse_performance(node))}
    description = page_description(soup)
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': VENUE,
            'city': CITY,
            'country_code': 'FR',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date, time_from in performances
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = sitemap_urls(session) | calendar_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(detail_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape TCE event detail',
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


class TheatreChampsElyseesFrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='theatrechampselysees_fr',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
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
    TheatreChampsElyseesFrCrawler().run()


if __name__ == '__main__':
    main()
