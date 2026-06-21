import re
from datetime import date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.stnicholas.cz'
ARCHIVE_URL = f'{BASE_URL}/kalendar-akci/'
SOURCE = 'Kostel sv. Mikuláše na Malé Straně'
SOURCE_URL = BASE_URL
DEFAULT_CITY = 'Praha'
DEFAULT_VENUE = 'Kostel sv. Mikuláše na Malé Straně'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

DATE_TIME_PATTERN = re.compile(
    r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\s+'
    r'(\d{1,2}):(\d{2})(?:\s*[-–]\s*(\d{1,2}):(\d{2}))?'
)
DATE_PATTERN = re.compile(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\b')
TIME_PATTERN = re.compile(r'\b(?:od|do)?\s*(\d{1,2}):(\d{2})\b', re.IGNORECASE)


def clean_text(text):
    if not text:
        return ''

    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_listing_datetime(text):
    match = DATE_TIME_PATTERN.search(text or '')
    if not match:
        return None, None, None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    time_from = f'{int(match.group(4)):02d}:{int(match.group(5)):02d}'
    time_to = None
    if match.group(6) and match.group(7):
        time_to = f'{int(match.group(6)):02d}:{int(match.group(7)):02d}'

    return f'{year}-{month:02d}-{day:02d}', time_from, time_to


def parse_detail_date(text):
    match = DATE_PATTERN.search(text or '')
    if not match:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    return f'{year}-{month:02d}-{day:02d}'


def parse_time(text):
    match = TIME_PATTERN.search(text or '')
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def text_from_selector(soup, selector):
    element = soup.select_one(selector)
    if not element:
        return None
    return clean_text(element.get_text('\n', strip=True)) or None


def extract_title(soup):
    title = text_from_selector(soup, 'h1.elementor-heading-title') or text_from_selector(soup, 'h1')
    if title:
        return title

    if soup.title:
        return clean_text(soup.title.get_text(' ', strip=True).split(' - ', 1)[0])
    return SOURCE


def extract_detail_field(soup, label):
    labels = soup.select('.interpret-first-col .elementor-heading-title')
    for label_el in labels:
        if clean_text(label_el.get_text(' ', strip=True)).casefold() != label.casefold():
            continue

        section = label_el.find_parent('section')
        if not section:
            continue

        columns = section.select(':scope > .elementor-container > .elementor-column')
        if len(columns) < 2:
            continue

        return clean_text(columns[1].get_text('\n', strip=True)) or None

    return None


def extract_program(soup):
    rows = []
    for row in soup.select('.program-row'):
        left = clean_text(row.select_one('.program-left').get_text(' ', strip=True)) if row.select_one('.program-left') else ''
        right = clean_text(row.select_one('.program-right').get_text('\n', strip=True)) if row.select_one('.program-right') else ''
        text = clean_text(' - '.join(part for part in [left, right] if part))
        if text and 'Předprodej online' not in text:
            rows.append(text)

    return '\n'.join(rows)


def extract_description(soup, title, date_value, time_from, time_to):
    authors = extract_detail_field(soup, 'Autoři')
    performers = extract_detail_field(soup, 'Interpreti')
    program = extract_program(soup)

    parts = [
        title,
        f'Datum: {date_value}' if date_value else None,
        f'Čas: {time_from} - {time_to}' if time_from and time_to else f'Čas: {time_from}' if time_from else None,
        f'Autoři: {authors}' if authors else None,
        f'Interpreti: {performers}' if performers else None,
        'Program:',
        program,
    ]
    return clean_text('\n\n'.join(part for part in parts if part)) or None


def find_event_links(session):
    links = {}
    page = 1

    while True:
        url = ARCHIVE_URL if page == 1 else urljoin(ARCHIVE_URL, f'page/{page}/')
        soup = get_soup(session, url)

        items = soup.select('.dce-events__item a.dce-events__link[href]')
        if not items:
            break

        for link in items:
            item = link.find_parent('li')
            listing_text = clean_text(item.get_text('\n', strip=True)) if item else clean_text(link.get_text('\n', strip=True))
            event_url = urljoin(BASE_URL, link.get('href'))
            links[event_url] = listing_text

        next_link = soup.select_one('.dc-events__pagination a.next[href]')
        if not next_link:
            break
        page += 1

    return links


def extract_concert(session, url, listing_text):
    listing_date, listing_time_from, listing_time_to = parse_listing_datetime(listing_text)
    soup = get_soup(session, url)

    title = extract_title(soup)
    detail_date = parse_detail_date(text_from_selector(soup, '.elementor-element-643609d') or '')
    time_from = parse_time(extract_detail_field(soup, 'Čas') or '') or listing_time_from
    time_to = parse_time((extract_detail_field(soup, 'Čas') or '').split('do ', 1)[-1]) if 'do ' in (extract_detail_field(soup, 'Čas') or '') else None
    time_to = time_to or listing_time_to
    date_value = detail_date or listing_date

    if not date_value:
        return None
    if date_value < date.today().isoformat():
        return None

    return {
        'title': title,
        'date': date_value,
        'url': url,
        'time_from': time_from,
        'time_to': time_to,
        'venue': DEFAULT_VENUE,
        'city': DEFAULT_CITY,
        'description': extract_description(soup, title, date_value, time_from, time_to),
        'type': 'concert',
    }


def get_concerts():
    session = requests.Session()
    links = find_event_links(session)
    concerts = []

    for url, listing_text in links.items():
        try:
            concert = extract_concert(session, url, listing_text)
        except requests.RequestException as exc:
            print(f'Failed to scrape {url}: {exc}')
            continue

        if concert:
            concerts.append(concert)

    return concerts


class StNicholasCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='stnicholas_cz',
        source=SOURCE,
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
            'description',
            'type',
        ],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    StNicholasCrawler().run()


if __name__ == '__main__':
    main()
