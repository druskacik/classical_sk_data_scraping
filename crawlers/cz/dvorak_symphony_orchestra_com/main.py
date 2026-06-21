import re
from datetime import date
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.dvorak-symphony-orchestra.com'
SOURCE_URL = BASE_URL
SOURCE_NAME = 'Dvořák Symphony Orchestra Prague'
HOME_URL = f'{BASE_URL}/cz/'
PROGRAM_INDEX_URL = f'{BASE_URL}/cz/program/'
DEFAULT_CITY = 'Praha'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

CZECH_MONTHS = {
    'ledna': 1,
    'února': 2,
    'unora': 2,
    'března': 3,
    'brezna': 3,
    'dubna': 4,
    'května': 5,
    'kvetna': 5,
    'června': 6,
    'cervna': 6,
    'července': 7,
    'cervence': 7,
    'srpna': 8,
    'září': 9,
    'zari': 9,
    'října': 10,
    'rijna': 10,
    'listopadu': 11,
    'prosince': 12,
}

VENUE_BY_SLUG = {
    'smetanova-sin': 'Smetanova síň',
    'kostel-svateho-jilji': 'Kostel sv. Jiljí',
    'klementinum': 'Klementinum',
    'gregruv-sal': 'Grégrův sál',
}


def clean_text(text):
    if not text:
        return ''
    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, timeout=20)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def program_year(url, soup):
    text = clean_text(soup.select_one('h2').get_text(' ', strip=True)) if soup.select_one('h2') else ''
    match = re.search(r'\b(20\d{2})\b', text) or re.search(r'program-(20\d{2})-', url)
    return int(match.group(1)) if match else None


def parse_date_time(text, year):
    match = re.search(
        r'(\d{1,2})\.\s*([A-Za-zÁ-žá-ž]+)\s+(\d{1,2})[:.](\d{2})',
        clean_text(text).lower(),
    )
    if not match or not year:
        return None, None

    month = CZECH_MONTHS.get(match.group(2))
    if not month:
        return None, None

    return date(year, month, int(match.group(1))).isoformat(), f'{int(match.group(3)):02d}:{match.group(4)}'


def venue_from_url(url):
    slug = urlparse(url).path.strip('/').split('/')[-1]
    for slug_part, venue in VENUE_BY_SLUG.items():
        if slug_part in slug:
            return venue
    return None


def discover_program_urls(session):
    urls = []
    for page_url in (HOME_URL, PROGRAM_INDEX_URL):
        soup = get_soup(session, page_url)
        for link in soup.select('a[href]'):
            href = link.get('href')
            if not href:
                continue
            url = urljoin(page_url, href)
            path = urlparse(url).path
            if re.search(r'/cz/program-\d{4}-', path):
                urls.append(url)

    unique_urls = sorted(set(urls), reverse=True)
    current_year = date.today().year
    current_urls = [
        url for url in unique_urls
        if (match := re.search(r'program-(20\d{2})-', url)) and int(match.group(1)) >= current_year
    ]
    if current_urls:
        return current_urls

    years = [
        int(match.group(1))
        for url in unique_urls
        if (match := re.search(r'program-(20\d{2})-', url))
    ]
    if not years:
        return unique_urls

    latest_year = max(years)
    return [url for url in unique_urls if f'program-{latest_year}-' in url]


def event_headers(soup):
    selector = '.nazev_udalosti, .nazev_udalosti_odd'
    return [header for header in soup.select(selector) if isinstance(header, Tag)]


def description_from_detail(title, date_text, venue, detail):
    detail_text = clean_text(detail.get_text('\n', strip=True))
    buy_link = detail.select_one('a.tlacitko_buy[href]')
    parts = [
        title,
        f'Datum a místo: {date_text} - {venue or DEFAULT_CITY}',
        detail_text,
        f'Vstupenky: {buy_link.get("href")}' if buy_link else '',
    ]
    return clean_text('\n\n'.join(part for part in parts if part)) or None


def extract_concerts_from_program(session, url):
    soup = get_soup(session, url)
    year = program_year(url, soup)
    venue = venue_from_url(url)
    concerts = []

    for header in event_headers(soup):
        header_text = clean_text(header.get_text(' ', strip=True))
        match = re.match(r'(?P<date>.+?\d{1,2}[:.]\d{2})\s+:\s+(?P<title>.+)$', header_text)
        if not match:
            continue

        concert_date, time_from = parse_date_time(match.group('date'), year)
        if not concert_date:
            continue

        detail_id = None
        onclick = header.get('onclick') or ''
        onclick_match = re.search(r"prekliknoutDen\('([^']+)'", onclick)
        if onclick_match:
            detail_id = onclick_match.group(1)

        detail = soup.find(id=detail_id) if detail_id else header.find_next_sibling('div')
        if not isinstance(detail, Tag):
            detail = header.find_next_sibling('div')
        if not isinstance(detail, Tag):
            continue

        title = clean_text(match.group('title'))
        concerts.append(
            {
                'title': title,
                'date': concert_date,
                'url': f'{url}#{header.get("id")}' if header.get('id') else url,
                'time_from': time_from,
                'time_to': None,
                'venue': venue,
                'city': DEFAULT_CITY,
                'type': 'concert',
                'description': description_from_detail(title, match.group('date'), venue, detail),
            }
        )

    return concerts


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = []
    for url in discover_program_urls(session):
        concerts.extend(extract_concerts_from_program(session, url))

    today = date.today().isoformat()
    upcoming = [concert for concert in concerts if concert['date'] >= today]
    return sorted(upcoming, key=lambda concert: (concert['date'], concert.get('time_from') or '', concert['title']))


class DvorakSymphonyOrchestraCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='dvorak_symphony_orchestra_com',
        source=SOURCE_NAME,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'time_to',
            'venue',
            'city',
            'type',
            'description',
        ],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    DvorakSymphonyOrchestraCrawler().run()


if __name__ == '__main__':
    main()
