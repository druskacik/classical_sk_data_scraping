import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://letnislavnosti.cz'
PROGRAM_URL = f'{BASE_URL}/program-koncertu/'
SOURCE_NAME = 'Letní slavnosti staré hudby'
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
    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_czech_datetime(text):
    text = clean_text(text)
    match = re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\s*\|\s*(\d{1,2})[.:](\d{2})\b', text)
    if not match:
        return None, None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    start = datetime(year, month, day, hour, minute)
    return start.date().isoformat(), start.strftime('%H:%M')


def parse_time_to(text):
    text = clean_text(text)
    match = re.search(r'[–-]\s*(\d{1,2})[.:](\d{2})\b', text)
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{int(match.group(2)):02d}'


def infer_city(*texts):
    combined = clean_text('\n'.join(text for text in texts if text))
    if re.search(r'\bPraha\b', combined, re.IGNORECASE):
        return 'Praha'
    return DEFAULT_CITY


def extract_card(card):
    link = card.select_one('h3.concert-title a[href], a.card__image[href*="/koncert/"]')
    time_el = card.select_one('.concert-info time')
    venue_el = card.select_one('.concert-info address')
    subtitle_el = card.select_one('.card__hover')

    if not link or not time_el:
        return None

    date, time_from = parse_czech_datetime(time_el.get_text(' ', strip=True))
    if not date:
        return None

    return {
        'title': clean_text(link.get_text(' ', strip=True)),
        'date': date,
        'url': urljoin(BASE_URL, link.get('href')),
        'time_from': time_from,
        'time_to': None,
        'venue': clean_text(venue_el.get_text(' ', strip=True)) if venue_el else None,
        'city': DEFAULT_CITY,
        'description': clean_text(subtitle_el.get_text('\n', strip=True)) if subtitle_el else None,
        'type': 'concert',
    }


def extract_listing_concerts(session):
    soup = get_soup(session, PROGRAM_URL)
    concerts = []

    for card in soup.select('article.card'):
        concert = extract_card(card)
        if concert:
            concerts.append(concert)

    return concerts


def first_text(soup, selector):
    element = soup.select_one(selector)
    if not element:
        return ''
    return clean_text(element.get_text('\n', strip=True))


def section_after_heading(soup, heading_text):
    heading = None
    for candidate in soup.select('h3.divider'):
        if clean_text(candidate.get_text(' ', strip=True)).lower() == heading_text.lower():
            heading = candidate
            break

    if not heading:
        return ''

    parts = []
    for section in heading.find_next_siblings():
        if section.name == 'h3' and 'divider' in section.get('class', []):
            break
        for removable in section.select('button, script, style, noscript'):
            removable.decompose()
        text = clean_text(section.get_text('\n', strip=True))
        if text:
            parts.append(text)

    return clean_text('\n\n'.join(parts))


def extract_header_data(soup):
    address = soup.select_one('address.address--concert')
    if not address:
        return {}

    date_time = first_text(address, 'span')
    date, time_from = parse_czech_datetime(date_time)

    venue_link = address.select_one('a[href*="/koncertni-sal/"]')
    venue = clean_text(venue_link.get_text(' ', strip=True)) if venue_link else None

    address_links = address.select('a[href]')
    address_text = clean_text(address_links[-1].get_text('\n', strip=True)) if len(address_links) > 1 else ''

    duration_text = first_text(address, '.address--concert--duration')

    return {
        'date': date,
        'time_from': time_from,
        'time_to': parse_time_to(duration_text),
        'venue': venue,
        'city': infer_city(address_text, venue),
        'address': address_text,
        'duration': duration_text,
    }


def extract_detail(session, url):
    soup = get_soup(session, url)
    title = first_text(soup, 'h1')
    subtitle = first_text(soup, '.concert-description')
    header = extract_header_data(soup)

    artists = section_after_heading(soup, 'Umělci')
    program = section_after_heading(soup, 'Program')
    annotation = first_text(soup, '.annotation #description-text') or section_after_heading(soup, 'Anotace')
    hall = first_text(soup, '.single-concert--hall__info')
    artist_bios = first_text(soup, '.single-concert__artists-details')

    description_parts = [
        title,
        subtitle,
        f'Datum a místo: {header.get("address")}' if header.get('address') else '',
        f'Čas: {header.get("duration")}' if header.get('duration') else '',
        f'Umělci:\n{artists}' if artists else '',
        f'Program:\n{program}' if program else '',
        f'Anotace:\n{annotation}' if annotation else '',
        f'Koncertní sál:\n{hall}' if hall else '',
        artist_bios,
    ]

    return {
        'title': title or None,
        'date': header.get('date'),
        'time_from': header.get('time_from'),
        'time_to': header.get('time_to'),
        'venue': header.get('venue'),
        'city': header.get('city') or DEFAULT_CITY,
        'description': clean_text('\n\n'.join(part for part in description_parts if part)) or None,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = extract_listing_concerts(session)
    details_cache = {}

    for concert in concerts:
        url = concert['url']
        if url not in details_cache:
            try:
                details_cache[url] = extract_detail(session, url)
            except requests.RequestException as exc:
                print(f'Failed to scrape {url}: {exc}')
                details_cache[url] = {}

        details = details_cache[url]
        for field in ['title', 'date', 'time_from', 'time_to', 'venue', 'city', 'description']:
            concert[field] = details.get(field) or concert.get(field)

    return [concert for concert in concerts if concert.get('title') and concert.get('date')]


class LetniSlavnostiCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='letnislavnosti_cz',
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
            'description',
            'type',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    LetniSlavnostiCrawler().run()


if __name__ == '__main__':
    main()
