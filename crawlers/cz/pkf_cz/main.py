import re
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.prgphil.cz'
ORIGINAL_URL = 'https://www.pkf.cz/'
LISTING_URL = f'{BASE_URL}/koncerty-a-vstupenky'
SOURCE_NAME = 'Prague Philharmonia'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

KNOWN_CITIES = [
    'Praha',
    'Litomyšl',
    'Český Krumlov',
    'Hradec Králové',
    'Murten',
]

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def clean_text(text):
    if not text:
        return ''
    text = text.replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_date(date_text):
    match = re.search(r'(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2,4})', date_text or '')
    if not match:
        return None

    day, month, year = match.groups()
    if len(year) == 2:
        year = f'20{year}'
    return f'{year}-{month.zfill(2)}-{day.zfill(2)}'


def parse_time(text):
    match = re.search(r'(\d{1,2}:\d{2})', text or '')
    return match.group(1) if match else None


def infer_city(*values):
    text = clean_text(' '.join(value for value in values if value))
    if not text:
        return None

    normalized = text.lower().replace('-', ' ').replace('_', ' ')
    normalized_ascii = (
        normalized.replace('á', 'a')
        .replace('č', 'c')
        .replace('ď', 'd')
        .replace('é', 'e')
        .replace('ě', 'e')
        .replace('í', 'i')
        .replace('ň', 'n')
        .replace('ó', 'o')
        .replace('ř', 'r')
        .replace('š', 's')
        .replace('ť', 't')
        .replace('ú', 'u')
        .replace('ů', 'u')
        .replace('ý', 'y')
        .replace('ž', 'z')
    )
    for city in KNOWN_CITIES:
        city_ascii = (
            city.lower()
            .replace('á', 'a')
            .replace('č', 'c')
            .replace('é', 'e')
            .replace('ě', 'e')
            .replace('í', 'i')
            .replace('ř', 'r')
            .replace('š', 's')
            .replace('ú', 'u')
            .replace('ý', 'y')
            .replace('ž', 'z')
        )
        if city.lower() in normalized or city_ascii in normalized_ascii:
            return city

    parts = [part.strip() for part in text.split(',') if part.strip()]
    if len(parts) >= 2:
        return parts[-1]

    return None


def get_soup(session, url):
    response = session.get(url, timeout=30, verify=False)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def first_text(parent, selectors):
    for selector in selectors:
        element = parent.select_one(selector)
        if element:
            text = clean_text(element.get_text(' ', strip=True))
            if text:
                return text
    return None


def extract_performers(card):
    performers = []
    for performer in card.select('.paragraph--interpret'):
        name = first_text(performer, ['.field--name-field-interpret'])
        role = first_text(performer, ['.field--name-field-nastroj'])
        if name and role:
            performers.append(f'{name} - {role}')
        elif name:
            performers.append(name)
    return performers


def extract_card(card):
    link = card.select_one('a.vypis-ko-hlavicka[href]') or card.select_one('h3 a[href]')
    if not link:
        return None

    date_text = first_text(card, ['.date-1'])
    date = parse_date(date_text)
    if not date:
        return None

    url = urljoin(BASE_URL, link.get('href'))
    time_from = parse_time(first_text(card, ['.date-2']))
    venue = first_text(card, ['.field--name-name'])
    title = first_text(card, ['h3.nadpis span', 'h3.nadpis', 'a.vypis-ko-hlavicka'])
    event_type = first_text(card, ['span.vypis-ko-cyklus'])
    performers = extract_performers(card)

    summary_parts = [part for part in [event_type, '\n'.join(performers)] if part]

    return {
        'title': title,
        'date': date,
        'time_from': time_from,
        'url': url,
        'venue': venue,
        'city': infer_city(venue, title, url),
        'type': event_type,
        'description': clean_text('\n\n'.join(summary_parts)) or None,
    }


def extract_detail_description(session, url):
    soup = get_soup(session, url)
    article = soup.select_one('article') or soup.select_one('.page-content')
    if not article:
        return None

    for element in article.select('script, style, nav, footer, .visually-hidden'):
        element.decompose()

    return clean_text(article.get_text('\n', strip=True)) or None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    soup = get_soup(session, LISTING_URL)
    concerts = []
    details_cache = {}

    for card in soup.select('div.vypis-ko-koncert'):
        concert = extract_card(card)
        if not concert or not concert['title']:
            continue

        url = concert['url']
        if url not in details_cache:
            try:
                details_cache[url] = extract_detail_description(session, url)
            except requests.RequestException as exc:
                print(f'Failed to scrape {url}: {exc}')
                details_cache[url] = None

        detail_description = details_cache[url]
        if detail_description:
            concert['description'] = detail_description

        concerts.append(concert)

    return concerts


class PkfCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='pkf_cz',
        source=SOURCE_NAME,
        source_url=ORIGINAL_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'time_from',
            'url',
            'venue',
            'city',
            'type',
            'description',
        ],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', ORIGINAL_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    PkfCrawler().run()


if __name__ == '__main__':
    main()
