import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


BASE_URL = 'https://www.jfo.cz/'
CALENDAR_URL = urljoin(BASE_URL, 'koncerty/kalendar-koncertu/')
SOURCE = 'Janáčkova filharmonie Ostrava'
DEFAULT_CITY = 'Ostrava'
KNOWN_CITIES = {'Hukvaldy', 'Opava', 'Ostrava'}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'cs,en;q=0.8',
}


def clean_text(value):
    if not value:
        return ''
    value = unescape(value).replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[ \t\r\f\v]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def canonical_url(href):
    absolute = urljoin(BASE_URL, href)
    parts = urlsplit(absolute)
    path = f'{parts.path.rstrip("/")}/'
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or 'utf-8'
    return BeautifulSoup(response.text, 'html.parser')


def discover_concert_urls(session):
    """Follow the calendar's server-rendered pagination and collect all events."""
    page_url = CALENDAR_URL
    seen_pages = set()
    urls = []

    while page_url not in seen_pages:
        seen_pages.add(page_url)
        soup = get_soup(session, page_url)

        for link in soup.select('a[href*="/koncert/"]'):
            url = canonical_url(link.get('href', ''))
            if re.fullmatch(r'https://www\.jfo\.cz/koncert/[^/]+/', url):
                urls.append(url)

        next_link = next(
            (
                link
                for link in soup.select('a[href]')
                if 'Načíst další koncerty' in clean_text(link.get_text(' ', strip=True))
            ),
            None,
        )
        if not next_link:
            break

        next_url = urljoin(CALENDAR_URL, next_link.get('href', ''))
        if next_url in seen_pages:
            break
        page_url = next_url

    return list(dict.fromkeys(urls))


def parse_datetime(value):
    if not value:
        return None, None

    date_match = re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b', value)
    time_match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', value)
    date = None
    if date_match:
        try:
            date = datetime.strptime(date_match.group(), '%d. %m. %Y').date().isoformat()
        except ValueError:
            pass
    time_from = None
    if time_match:
        time_from = f'{int(time_match.group(1)):02d}:{time_match.group(2)}'
    return date, time_from


def extract_location(soup):
    element = soup.select_one('.list__info--place .list__info__text')
    location = clean_text(element.get_text(' ', strip=True)) if element else ''
    if not location:
        return None, DEFAULT_CITY

    parts = [part.strip() for part in location.split(',', 1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        if parts[0] in KNOWN_CITIES:
            return parts[1], parts[0]
        if parts[1] in KNOWN_CITIES:
            return parts[0], parts[1]
    return location, DEFAULT_CITY


def extract_concert(session, url):
    soup = get_soup(session, url)
    title_element = soup.select_one('h1.page__title')
    title = clean_text(title_element.get_text(' ', strip=True)) if title_element else ''

    datetime_element = soup.select_one(
        '.is-detail .list__info:not(.list__info--place) .list__info__text'
    )
    datetime_text = clean_text(datetime_element.get_text(' ', strip=True)) if datetime_element else ''
    date, time_from = parse_datetime(datetime_text)
    venue, city = extract_location(soup)

    description_element = soup.select_one('.page__content__text.page__content__col')
    description = (
        clean_text(description_element.get_text('\n', strip=True))
        if description_element
        else ''
    )

    if not title or not date:
        return None

    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'CZ',
        'description': description or None,
        'source_url': BASE_URL,
        'source': SOURCE,
    }


class JfoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='jfo_cz',
        source=SOURCE,
        source_url=BASE_URL,
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
        dedupe_subset=['url', 'date', 'time_from'],
    )

    def scrape(self):
        session = requests.Session()
        concerts = []

        for url in discover_concert_urls(session):
            try:
                concert = extract_concert(session, url)
            except requests.RequestException as exc:
                log_message('Failed to scrape event', event='crawler_item_failed', level='warning', url=url, error_type=type(exc).__name__, error_message=str(exc))
                continue
            if concert:
                concerts.append(concert)
            else:
                log_message('Skipping event with missing title or date', event='crawler_item_skipped', level='warning', url=url)

        return concerts


def main():
    JfoCrawler().run()


if __name__ == '__main__':
    main()
