import re
from datetime import datetime
from html import unescape
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://socr.rozhlas.cz'
LISTING_URL = f'{BASE_URL}/koncerty-a-vstupenky'
SOURCE_NAME = 'Symfonický orchestr Českého rozhlasu'
SOURCE_URL = BASE_URL
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


def clean_text(text):
    if not text:
        return ''
    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_czech_date(text):
    match = re.search(r'\b(\d{1,2})\.\s*([A-Za-zÁ-ž]+)\s*(20\d{2})\b', text or '', re.IGNORECASE)
    if not match:
        return None

    month = CZECH_MONTHS.get(match.group(2).lower())
    if not month:
        return None

    try:
        return datetime(int(match.group(3)), month, int(match.group(1))).date().isoformat()
    except ValueError:
        return None


def parse_time(text):
    match = re.search(r'\b(?:v|ve)\s*(\d{1,2})[.:](\d{2})\b', text or '', re.IGNORECASE)
    if not match:
        match = re.search(r'\b(\d{1,2})[.:](\d{2})\b', text or '')
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def split_venue(venue_text):
    venue_text = clean_text(venue_text)
    if not venue_text:
        return None

    first_sentence = re.split(
        r'\b(?:pondělí|úterý|středa|čtvrtek|pátek|sobota|neděle)\b',
        venue_text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    first_sentence = first_sentence.strip(' ,')
    return first_sentence or None


def infer_city(venue):
    text = clean_text(venue).lower()
    if 'karlín' in text or 'pražský hrad' in text or 'rudolfinum' in text:
        return 'Praha'
    if 'český rozhlas' in text or 'betlémská kaple' in text or 'obecní dům' in text:
        return 'Praha'
    return DEFAULT_CITY


def listing_url(page):
    if page == 0:
        return LISTING_URL
    return f'{LISTING_URL}?page={page}#b004f'


def get_page_count(soup):
    pages = {0}
    for link in soup.select('a[href*="page="]'):
        query = parse_qs(urlparse(link.get('href', '')).query)
        for value in query.get('page', []):
            if value.isdigit():
                pages.add(int(value))
    return max(pages) + 1


def extract_card(card):
    link = card.select_one('a[href]')
    if not link:
        return None

    url = urljoin(BASE_URL, link.get('href'))
    if '/koncerty-a-vstupenky' in url:
        return None

    title_el = card.select_one('h3 a[href], h2 a[href], .title a[href], a.title')
    title = clean_text(title_el.get_text(' ', strip=True)) if title_el else ''
    if not title:
        for candidate in card.select('a[href]'):
            text = clean_text(candidate.get_text(' ', strip=True))
            if text and text.lower() != 'pozvánka':
                title = text
                break

    card_text = clean_text(card.get_text('\n', strip=True))
    lines = [line for line in card_text.split('\n') if line and line.lower() != 'pozvánka']
    venue_line = next((line for line in lines if parse_czech_date(line)), '')
    date = parse_czech_date(venue_line or card_text)
    time_from = parse_time(venue_line or card_text)
    venue = split_venue(venue_line)

    if not title or not date:
        return None

    return {
        'title': title,
        'date': date,
        'time_from': time_from,
        'time_to': None,
        'url': url,
        'venue': venue,
        'city': infer_city(venue),
        'type': 'concert',
        'description': clean_text('\n'.join(lines)) or None,
    }


def extract_listing_concerts(session):
    first_page = get_soup(session, LISTING_URL)
    concerts = []

    for page in range(get_page_count(first_page)):
        soup = first_page if page == 0 else get_soup(session, listing_url(page))
        for card in soup.select('li.b-004__list-item'):
            concert = extract_card(card)
            if concert:
                concerts.append(concert)

    return list({concert['url']: concert for concert in concerts}.values())


def extract_detail_description(soup):
    title = clean_text(soup.select_one('h1').get_text(' ', strip=True)) if soup.select_one('h1') else ''
    date = clean_text(soup.select_one('.date, time').get_text(' ', strip=True)) if soup.select_one('.date, time') else ''

    lead_parts = []
    for selector in ['.field.field--name-field-perex', '.perex', '.b-detail__perex']:
        element = soup.select_one(selector)
        if element:
            lead_parts.append(clean_text(element.get_text('\n', strip=True)))

    body = soup.select_one('.field.body')
    if not body:
        body = soup.select_one('.content-container') or soup.select_one('.content')

    if body:
        for element in body.select(
            'script, style, noscript, form, iframe, .share, .social, .newsletter, .advert, .ads, .player'
        ):
            element.decompose()
        body_text = clean_text(body.get_text('\n', strip=True))
    else:
        body_text = ''

    return clean_text('\n\n'.join(part for part in [title, date, *lead_parts, body_text] if part)) or None


def extract_detail_data(session, concert):
    soup = get_soup(session, concert['url'])
    description = extract_detail_description(soup)

    detail_text = clean_text(soup.get_text('\n', strip=True))
    if not concert.get('time_from'):
        concert['time_from'] = parse_time(detail_text)
    if not concert.get('venue'):
        venue_line = next((line for line in detail_text.split('\n') if parse_czech_date(line)), '')
        concert['venue'] = split_venue(venue_line)
        concert['city'] = infer_city(concert['venue'])

    if description:
        concert['description'] = description

    return concert


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = extract_listing_concerts(session)
    enriched = []
    for concert in concerts:
        try:
            enriched.append(extract_detail_data(session, concert))
        except requests.RequestException as exc:
            print(f'Failed to scrape {concert["url"]}: {exc}')
            enriched.append(concert)
    return enriched


class SocrRozhlasCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='socr_rozhlas_cz',
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
    SocrRozhlasCrawler().run()


if __name__ == '__main__':
    main()
