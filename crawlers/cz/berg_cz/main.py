import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://berg.cz/'
SOURCE = 'Orchestr BERG'
SOURCE_URL = 'https://berg.cz'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
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
    text = text.replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser', from_encoding='windows-1250')


def discover_program_url(session):
    soup = get_soup(session, urljoin(BASE_URL, 'bergmenu_cs.js'))
    menu_text = soup.get_text('\n')
    match = re.search(r"makeMenu\('top3'.*?'(koncerty\d{2}\.html)'", menu_text)
    if match:
        return urljoin(BASE_URL, match.group(1))

    year_suffix = datetime.now().strftime('%y')
    return urljoin(BASE_URL, f'koncerty{year_suffix}.html')


def find_concert_links(session):
    program_url = discover_program_url(session)
    year_match = re.search(r'koncerty(\d{2})\.html', program_url)
    current_year_prefix = year_match.group(1) if year_match else datetime.now().strftime('%y')
    urls_to_scan = [program_url, BASE_URL]
    links = []

    for url in urls_to_scan:
        soup = get_soup(session, url)
        for link in soup.select('a[href]'):
            href = link.get('href', '').strip()
            if re.fullmatch(
                rf'{current_year_prefix}\d{{4}}(?:_[a-z0-9_-]+)?\.html',
                href,
                re.IGNORECASE,
            ):
                links.append(urljoin(BASE_URL, href))

    return list(dict.fromkeys(links))


def parse_date(text):
    text = clean_text(text).lower()
    pattern = r'(\d{1,2})\.\s*([a-záéíóúýčďěňřšťůž]+)\s*(\d{4})'
    match = re.search(pattern, text)
    if not match:
        return None

    day = int(match.group(1))
    month = CZECH_MONTHS.get(match.group(2))
    year = int(match.group(3))
    if not month:
        return None

    return f'{year}-{month:02d}-{day:02d}'


def parse_time(text):
    match = re.search(r'\b(\d{1,2}:\d{2})(?:\s*[-–]\s*(\d{1,2}:\d{2}))?', text)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def parse_venue(text):
    if '|' not in text:
        return None
    venue = clean_text(text.split('|', 1)[1])
    venue = re.split(r'\s*\(|\n', venue, maxsplit=1)[0].strip()
    if not venue or 'bude upřesněno' in venue.lower() or 'bude upresneno' in venue.lower():
        return None
    return venue


def infer_city(text):
    if re.search(r'\bPraha\b', text, re.IGNORECASE):
        return 'Praha'
    return 'Praha'


def find_meta_text(content):
    for strong in content.find_all('strong'):
        text = clean_text(strong.get_text(' ', strip=True))
        if re.search(r'\b20\d{2}\b', text):
            return text
    return ''


def extract_concert_info(session, url):
    soup = get_soup(session, url)
    content = soup.select_one('.text2 div[align="left"]') or soup.select_one('.text2')
    if not content:
        return None

    for table in content.find_all('table'):
        table.decompose()

    heading = content.find('h2')
    raw_title = clean_text(heading.get_text(' ', strip=True)) if heading else ''
    title = clean_text(raw_title.split('|', 1)[0])
    if not title:
        title = clean_text(soup.title.get_text(' ', strip=True)) if soup.title else SOURCE

    meta_text = find_meta_text(content)
    description = clean_text(content.get_text('\n', strip=True)) or None
    time_from, time_to = parse_time(meta_text)

    return {
        'title': title,
        'date': parse_date(meta_text),
        'url': url,
        'time_from': time_from,
        'time_to': time_to,
        'venue': parse_venue(meta_text),
        'city': infer_city(description or meta_text),
        'description': description,
        'type': 'concert',
    }


def validate_concert(concert):
    if not concert.get('date'):
        return False
    if concert.get('title') == '???':
        return False
    return True


class BergCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='berg_cz',
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
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        session = requests.Session()
        concert_links = find_concert_links(session)
        concert_data = []

        for link in concert_links:
            concert = extract_concert_info(session, link)
            if concert and validate_concert(concert):
                concert_data.append(concert)

        return concert_data


def main():
    BergCrawler().run()


if __name__ == '__main__':
    main()
