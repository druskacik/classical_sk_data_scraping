import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://cbso.co.uk/'
SOURCE = 'City of Birmingham Symphony Orchestra'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on')
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# Venue names on CBSO pages normally omit the town. These are touring venues
# seen in the calendar; all other CBSO listings are defensibly Birmingham-based.
VENUE_CITIES = {
    'artis-naples': 'Naples',
    'barbican': 'London',
    'bridgewater hall': 'Manchester',
    'bristol beacon': 'Bristol',
    'de montfort hall': 'Leicester',
    'glasgow royal concert hall': 'Glasgow',
    'g live': 'Guildford',
    'herbert art gallery': 'Coventry',
    'nottingham royal concert hall': 'Nottingham',
    'royal albert hall': 'London',
    'royal concert hall, nottingham': 'Nottingham',
    'royal festival hall': 'London',
    'sage gateshead': 'Gateshead',
    'the glasshouse': 'Gateshead',
    'usher hall': 'Edinburgh',
    'warwick arts centre': 'Coventry',
}

CITY_NAMES = (
    'Birmingham', 'London', 'Manchester', 'Nottingham', 'Leicester',
    'Coventry', 'Bristol', 'Edinburgh', 'Glasgow', 'Guildford', 'Gateshead',
    'Cheltenham', 'Worcester', 'Warwick', 'Naples',
)


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


def event_links(soup):
    return {
        canonical_url(link.get('href'))
        for link in soup.select('a[href*="/events/"]')
        if '/events/' in urlsplit(urljoin(SOURCE_URL, link.get('href'))).path
    }


def listing_urls(session):
    urls = set()
    previous_page_urls = None
    page_number = 1
    while True:
        url = LISTING_URL if page_number == 1 else f'{LISTING_URL}/page-{page_number}'
        soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
        page_urls = event_links(soup)
        if not page_urls or page_urls == previous_page_urls:
            break
        urls.update(page_urls)
        previous_page_urls = page_urls

        next_link = soup.select_one('a[rel="next"]')
        if not next_link:
            next_link = next(
                (link for link in soup.select('a[href]')
                 if clean_text(link).casefold().startswith('next')),
                None,
            )
        if not next_link:
            break
        page_number += 1
    return urls


def sitemap_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    return {
        canonical_url(node.get_text(strip=True))
        for node in soup.select('url > loc')
        if '/events/' in node.get_text(strip=True)
    }


def meta_value(soup, key):
    wanted = key.casefold()
    for item in soup.select('.c-meta__item'):
        label = clean_text(item.select_one('.c-meta__key')).casefold()
        if label == wanted:
            return item.select_one('.c-meta__value')
    return None


def resolve_city(venue, title):
    location = f'{venue} {title}'
    for city in CITY_NAMES:
        if re.search(rf'\b{re.escape(city)}\b', location, re.I):
            return city
    folded = venue.casefold()
    for venue_name, city in VENUE_CITIES.items():
        if venue_name in folded:
            return city
    return 'Birmingham'


def page_description(soup):
    # The first content section contains the introduction and programme. Later
    # sections contain performer cards and recommendations rather than prose.
    node = soup.select_one(
        '.o-sidebar-grid__content > section:first-of-type '
        '.o-grid__item.o-block'
    )
    return clean_text(node) or None


def detail_record(session, url):
    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
    title = clean_text(soup.select_one('.c-page-header__title') or soup.find('h1'))
    venue = clean_text(meta_value(soup, 'Venue'))
    time_node = meta_value(soup, 'Date/Time')
    time_node = time_node.select_one('time[datetime]') if time_node else None
    value = time_node.get('datetime', '') if time_node else ''
    if not title or not venue or not value:
        return None
    try:
        start = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None

    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': url,
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': resolve_city(venue, title),
        'country_code': 'GB',
        'description': page_description(soup),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session) | sitemap_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(detail_record, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape CBSO concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class CbsoCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cbso_co_uk',
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
    CbsoCoUkCrawler().run()


if __name__ == '__main__':
    main()
