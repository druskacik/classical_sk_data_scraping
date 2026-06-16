import html
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://neoklasikorchestr.cz'
PAGES_API_URL = f'{BASE_URL}/wp-json/wp/v2/pages'
SOURCE = 'NeoKlasik orchestr'
SOURCE_URL = BASE_URL
DEFAULT_CITY = 'Praha'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
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

KNOWN_CITIES = [
    'Praha',
    'Poděbrady',
    'Podebrady',
    'Kralovice',
    'Mariánský Týnec',
    'Mariansky Tynec',
    'Prachatice',
    'Plzeň',
    'Terezín',
]

PRAGUE_DISTRICTS = {
    'Karlín',
    'Malá Strana',
    'Nové Město',
    'Staré Město',
    'Vinohrady',
    'Žižkov',
}


def clean_text(text):
    if not text:
        return ''
    text = html.unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def strip_content(html_content):
    soup = BeautifulSoup(html_content or '', 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    return soup


def page_lines(soup):
    text = clean_text(soup.get_text('\n'))
    return [line for line in text.split('\n') if line.strip()]


def extract_title(soup, fallback):
    heading = soup.select_one('h1, h2, .elementor-heading-title')
    title = clean_text(heading.get_text(' ', strip=True)) if heading else ''
    return title or clean_text(fallback)


def value_after_label(lines, label):
    for index, line in enumerate(lines):
        if line.strip().lower() == label.lower():
            for value in lines[index + 1:]:
                value = clean_text(value)
                if value:
                    return value
    return None


def parse_date(text):
    text = clean_text(text).lower()
    numeric_match = re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\b', text)
    if numeric_match:
        day = int(numeric_match.group(1))
        month = int(numeric_match.group(2))
        year = int(numeric_match.group(3))
        return f'{year}-{month:02d}-{day:02d}'

    named_match = re.search(r'\b(\d{1,2})\.\s*([a-záéíóúýčďěňřšťůž]+)\s+(20\d{2})\b', text)
    if named_match:
        day = int(named_match.group(1))
        month = CZECH_MONTHS.get(named_match.group(2))
        year = int(named_match.group(3))
        if month:
            return f'{year}-{month:02d}-{day:02d}'

    return None


def parse_time(text):
    if not text:
        return None
    match = re.search(r'\b(\d{1,2}:\d{2})\b', text)
    return match.group(1) if match else None


def normalize_city(city):
    if not city:
        return None
    city = clean_text(city)
    city = re.sub(r'\s+\d+.*$', '', city)
    city = re.sub(r'\s*[,-].*$', '', city)
    if city.startswith('Praha'):
        return 'Praha'
    if city in PRAGUE_DISTRICTS:
        return 'Praha'
    if city == 'Podebrady':
        return 'Poděbrady'
    if city.startswith('Poděbrady'):
        return 'Poděbrady'
    if city == 'Mariansky Tynec':
        return 'Mariánský Týnec'
    return city


def infer_city(*texts):
    combined = clean_text('\n'.join(text for text in texts if text))

    postal_match = re.search(
        r'\b\d{3}\s?\d{2}[ \t]+([A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][^\n,]+)',
        combined,
    )
    if postal_match:
        return normalize_city(postal_match.group(1))

    if re.search(r'\bPraha\b[ \t]+\d{3}\s?\d{2}\b', combined, re.IGNORECASE):
        return 'Praha'

    for city in KNOWN_CITIES:
        if re.search(rf'\b{re.escape(city)}\b', combined, re.IGNORECASE):
            return normalize_city(city)

    return DEFAULT_CITY


def extract_description(lines):
    description_start = None
    stop_labels = {'partneři koncertu'}

    for index, line in enumerate(lines):
        if line.strip().lower() == 'popis koncertu':
            description_start = index + 1
            break

    if description_start is None:
        return clean_text('\n'.join(lines)) or None

    description_lines = []
    for line in lines[description_start:]:
        if line.strip().lower() in stop_labels:
            break
        description_lines.append(line)

    return clean_text('\n'.join(description_lines)) or None


def is_concert_page(lines, page):
    if 'koncert-test' in page.get('slug', ''):
        return False
    if '/en/' in page.get('link', ''):
        return False

    labels = {line.strip().lower() for line in lines}
    return {'datum', 'začátek', 'místo'}.issubset(labels)


def fetch_pages(session):
    page_number = 1
    pages = []

    while True:
        response = session.get(
            PAGES_API_URL,
            params={
                'per_page': 100,
                'page': page_number,
                '_fields': 'id,slug,link,title,content',
            },
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        pages.extend(batch)

        total_pages = int(response.headers.get('X-WP-TotalPages', page_number))
        if page_number >= total_pages:
            break
        page_number += 1

    return pages


def extract_concert(page):
    soup = strip_content(page.get('content', {}).get('rendered', ''))
    lines = page_lines(soup)
    if not is_concert_page(lines, page):
        return None

    title = extract_title(soup, page.get('title', {}).get('rendered'))
    date_text = value_after_label(lines, 'Datum')
    time_text = value_after_label(lines, 'Začátek')
    venue = value_after_label(lines, 'Místo')
    address = value_after_label(lines, 'Adresa')
    description = extract_description(lines)

    concert = {
        'title': title,
        'date': parse_date(date_text),
        'url': urljoin(BASE_URL, page.get('link', '')),
        'time_from': parse_time(time_text),
        'time_to': None,
        'venue': clean_text(venue) or None,
        'city': infer_city(address, venue, title, page.get('link', '')),
        'description': description,
        'type': 'concert',
    }

    if not concert['date']:
        return None

    return concert


def get_concerts():
    session = requests.Session()
    pages = fetch_pages(session)
    concerts = []

    for page in pages:
        concert = extract_concert(page)
        if concert:
            concerts.append(concert)

    return concerts


class NeoklasikCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='neoklasikorchestr_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
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
    NeoklasikCrawler().run()


if __name__ == '__main__':
    main()
