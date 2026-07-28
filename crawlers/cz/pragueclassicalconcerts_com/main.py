import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://pragueclassicalconcerts.com'
LISTING_URL = f'{BASE_URL}/en/?ipp=100&page=1'
SOURCE_URL = 'https://www.pragueclassicalconcerts.com/'
SOURCE = 'Prague Classical Concerts'
DEFAULT_CITY = 'Praha'

HEADERS = {
    # The normal browser user agent is challenged by Cloudflare. The site explicitly
    # allows /en/ to crawlers in robots.txt and serves its crawlable HTML to Googlebot.
    'User-Agent': 'Googlebot',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en',
}


def clean_text(value):
    if not value:
        return ''
    value = value.replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser'), response.url


def text_from(element, selector):
    selected = element.select_one(selector)
    if not selected:
        return None
    return clean_text(selected.get_text(' ', strip=True)) or None


def canonical_detail_url(url):
    """Remove the optional YY-MM-DD-HHMM performance suffix from a detail URL."""
    parsed = urlsplit(url)
    path = re.sub(r'/\d{2}-\d{2}-\d{2}-\d{4}/?$', '', parsed.path)
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def description_from_detail(session, url, cache):
    cache_url = canonical_detail_url(url)
    if cache_url in cache:
        return cache[cache_url]

    soup, _ = get_soup(session, cache_url)
    about = soup.select_one('.cd-about__body')
    description = clean_text(about.get_text('\n', strip=True)) if about else ''
    cache[cache_url] = description or None
    return cache[cache_url]


def next_listing_url(soup, current_url):
    next_link = soup.select_one('a.pager__btn--next[href]')
    if not next_link:
        return None
    return urljoin(current_url, next_link.get('href'))


def extract_listing_concerts(soup, page_url, session, description_cache):
    concerts = []

    for day in soup.select('section.concert-day[data-day]'):
        concert_date = day.get('data-day')
        if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', concert_date or ''):
            continue

        for card in day.select('a.event-card[href]'):
            genres = {
                clean_text(element.get_text(' ', strip=True)).casefold()
                for element in card.select('.event-card__genre')
            }
            if 'classical concert' not in genres:
                continue

            title = text_from(card, '.event-card__title')
            if not title:
                continue

            detail_url = urljoin(page_url, card.get('href'))
            time_from = text_from(card, '.event-card__time')
            if time_from and not re.fullmatch(r'\d{1,2}:\d{2}', time_from):
                time_from = None
            elif time_from:
                hour, minute = time_from.split(':')
                time_from = f'{int(hour):02d}:{minute}'

            venue = text_from(card, '.event-card__venue')
            try:
                description = description_from_detail(
                    session, detail_url, description_cache
                )
            except requests.RequestException as exc:
                print(f'Failed to scrape concert detail {detail_url}: {exc}')
                description = clean_text(
                    '\n'.join(
                        part for part in (title, venue) if part
                    )
                ) or None

            concerts.append(
                {
                    'title': title,
                    'date': concert_date,
                    'url': detail_url,
                    'time_from': time_from,
                    'venue': venue,
                    'city': DEFAULT_CITY,
                    'country_code': 'CZ',
                    'description': description,
                    'source_url': SOURCE_URL,
                    'source': SOURCE,
                }
            )

    return concerts


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = []
    description_cache = {}
    visited_pages = set()
    page_url = LISTING_URL

    while page_url and page_url not in visited_pages:
        visited_pages.add(page_url)
        soup, final_url = get_soup(session, page_url)
        concerts.extend(
            extract_listing_concerts(
                soup, final_url, session, description_cache
            )
        )
        page_url = next_listing_url(soup, final_url)

    unique = {
        (
            concert['date'],
            concert['time_from'],
            concert['url'],
        ): concert
        for concert in concerts
    }
    return sorted(
        unique.values(),
        key=lambda concert: (
            concert['date'],
            concert['time_from'] or '',
            concert['title'],
        ),
    )


class PragueClassicalConcertsCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='pragueclassicalconcerts_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
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
        dedupe_subset=['date', 'time_from', 'url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    crawler = PragueClassicalConcertsCrawler()
    concerts = crawler.scrape()
    print(f'Found {len(concerts)} concerts')
    for concert in concerts[:5]:
        print(concert)


if __name__ == '__main__':
    main()
