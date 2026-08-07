import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.mayflower.org.uk/'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on/')
SOURCE = 'Mayflower'
CITY = 'Southampton'

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


def event_links(soup):
    return {
        urljoin(SOURCE_URL, link.get('href'))
        for link in soup.select('a.c-event-card__permalink[href]')
        if link.get('href')
    }


def listing_urls(session):
    # Mayflower is a mixed theatre calendar. Music and opera are separate
    # public filters, both rendered in the initial HTML response.
    urls = set()
    for genre in ('music-concerts', 'opera'):
        page = 1
        while True:
            path = 'whats-on/' if page == 1 else f'whats-on/page/{page}/'
            soup = get_soup(session, urljoin(SOURCE_URL, path), params={'genre[]': genre})
            page_urls = event_links(soup)
            urls.update(page_urls)
            next_link = soup.select_one('a.next.page-numbers[href]')
            if not page_urls or not next_link:
                break
            page += 1
    return urls


def parse_date(value):
    value = re.sub(r'(\d)(st|nd|rd|th)\b', r'\1', clean_text(value), flags=re.I)
    try:
        return datetime.strptime(value, '%d %B %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    value = clean_text(value).replace('.', ':').upper()
    for pattern in ('%I:%M%p', '%I%p', '%H:%M'):
        try:
            return datetime.strptime(value.replace(' ', ''), pattern).strftime('%H:%M')
        except ValueError:
            continue
    return None


def detail_description(soup):
    parts = []
    selectors = (
        '.c-event-details__synopsis',
        'main .c-container .c-col-text-area.c-wysiwyg',
    )
    for selector in selectors:
        for element in soup.select(selector):
            text = clean_text(element)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def detail_records(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.select_one('h1.c-masthead__title'))
    venue_element = soup.select_one('.c-event-details__venue p')
    venue = clean_text(venue_element)
    description = detail_description(soup)
    if not title or not venue:
        return []

    records = []
    seen = set()
    for instance in soup.select('.c-instances__list-item'):
        event_date = parse_date(instance.select_one('.c-instances__list-date'))
        time_from = parse_time(instance.select_one('.c-instances__list-time'))
        if not event_date:
            continue
        identity = (event_date, time_from)
        if identity in seen:
            continue
        seen.add(identity)
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
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
                    'Failed to scrape Mayflower event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda item: (item['date'], item['time_from'] or '', item['title'], item['url']),
    )


class MayflowerOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mayflower_org_uk',
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
    MayflowerOrgUkCrawler().run()


if __name__ == '__main__':
    main()
