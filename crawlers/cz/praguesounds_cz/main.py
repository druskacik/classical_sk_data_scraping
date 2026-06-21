import re
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://praguesounds.cz'
PROGRAM_URL = f'{BASE_URL}/cs/program'
SOURCE = 'Prague Sounds'
SOURCE_URL = BASE_URL
DEFAULT_CITY = 'Praha'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def clean_text(text):
    if not text:
        return ''
    text = unescape(text).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    text = re.sub(r'\s+([.,;:!?])', r'\1', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def discover_event_links(session):
    soup = get_soup(session, PROGRAM_URL)
    links = []

    for link in soup.select('a.page__news-link[href*="/cs/event/"]'):
        href = link.get('href')
        if href:
            links.append(urljoin(BASE_URL, href))

    if not links:
        for link in soup.select('a[href*="/cs/event/"]'):
            href = link.get('href')
            if href:
                links.append(urljoin(BASE_URL, href))

    return list(dict.fromkeys(links))


def parse_date_time(text):
    text = clean_text(text)
    match = re.search(r'\b(\d{1,2})\s+(\d{1,2})\s+(20\d{2})(?:\s+(\d{1,2}:\d{2}))?', text)
    if not match:
        return None, None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    time_from = match.group(4)
    return f'{year}-{month:02d}-{day:02d}', time_from


def title_from_soup(soup, fallback):
    event_items = soup.select('.event__box-item')
    if len(event_items) >= 2:
        title = clean_text(event_items[1].get_text(' ', strip=True))
        if title:
            return title

    if soup.title and soup.title.string:
        return clean_text(soup.title.string.split('|', 1)[0])

    return fallback


def extract_description(soup):
    parts = []

    for text_block in soup.select('.canvas.canvas--white .text'):
        text = clean_text(text_block.get_text(' ', strip=True))
        if not text:
            continue
        lower_text = text.lower()
        if 'zobrazit na mapě' in lower_text:
            continue
        if 'newsletter' in lower_text or 'zpracováním osobních údajů' in lower_text:
            continue
        parts.append(text)

    return clean_text('\n\n'.join(parts)) or None


def extract_venue_address(soup):
    venue = None
    address = None

    event_items = soup.select('.event__box-item')
    if len(event_items) >= 3:
        venue = clean_text(event_items[2].get_text(' ', strip=True)) or None

    venue_block = soup.select_one('.event__venue')
    if venue_block:
        venue_title = venue_block.select_one('.title-2')
        if venue_title:
            venue = clean_text(venue_title.get_text(' ', strip=True)) or venue

        address_block = venue_block.select_one('.text')
        if address_block:
            address = clean_text(address_block.get_text(' ', strip=True))
            address = re.sub(r'\s*zobrazit na mapě\s*→?$', '', address, flags=re.IGNORECASE).strip()

    return venue, address or None


def extract_concert(session, url):
    soup = get_soup(session, url)
    event_items = soup.select('.event__box-item')
    date_text = clean_text(event_items[0].get_text(' ', strip=True)) if event_items else ''
    date, time_from = parse_date_time(date_text)

    title = title_from_soup(soup, fallback=SOURCE)
    venue, address = extract_venue_address(soup)
    description = extract_description(soup)
    if address:
        description = clean_text(f'{description}\n\n{address}' if description else address)

    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': venue,
        'city': DEFAULT_CITY,
        'description': description,
        'type': 'concert',
    }


def get_concerts():
    session = requests.Session()
    concert_links = discover_event_links(session)
    concerts = []

    for link in concert_links:
        try:
            concert = extract_concert(session, link)
        except requests.RequestException as exc:
            print(f'Failed to scrape {link}: {exc}')
            continue

        if not concert.get('date'):
            print(f'Skipping {link}: missing date')
            continue
        concerts.append(concert)

    return concerts


class PragueSoundsCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='praguesounds_cz',
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
        dedupe_subset=['title', 'date', 'time_from'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    PragueSoundsCrawler().run()


if __name__ == '__main__':
    main()
