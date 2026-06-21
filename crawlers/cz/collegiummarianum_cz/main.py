import re
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.collegiummarianum.cz'
CONCERTS_URL = f'{BASE_URL}/koncerty/'
SOURCE = 'Collegium Marianum'
SOURCE_URL = BASE_URL

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

FOOTER_STARTS = {
    'facebook',
    'collegium marianum',
    'aktuální koncerty',
    'programy',
    'jana semerádová',
    'diskografie',
    'o souboru',
    'kontakt',
    'novinky z collegia',
}

PRAGUE_DISTRICTS = {
    'Anežský klášter',
    'Břevnovský klášter',
    'Dušní ul.',
    'kostel sv. Šimona a Judy',
    'Strahovský klášter',
}


def clean_text(text):
    if not text:
        return ''

    text = unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def find_concert_links(session):
    soup = get_soup(session, CONCERTS_URL)
    links = []

    for link in soup.select('a[href]'):
        href = urljoin(BASE_URL, link.get('href', '').strip())
        if not re.match(rf'{re.escape(CONCERTS_URL)}[^/#?]+/?$', href):
            continue
        links.append(href)

    return list(dict.fromkeys(links))


def parse_datetime(text):
    match = re.search(
        r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\s*\|\s*(\d{1,2}):(\d{2})\b',
        text,
    )
    if not match:
        return None, None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    return f'{year}-{month:02d}-{day:02d}', f'{hour:02d}:{minute:02d}'


def extract_content_lines(soup):
    main = soup.select_one('main') or soup.select_one('#fl-main-content') or soup.body
    content = BeautifulSoup(str(main), 'html.parser')

    for tag in content.select('script, style, noscript, header, footer, nav, form, img, svg'):
        tag.decompose()

    lines = []
    for line in clean_text(content.get_text('\n', strip=True)).split('\n'):
        line = clean_text(line)
        if line:
            lines.append(line)

    return lines


def first_index(lines, value):
    value = value.lower()
    for index, line in enumerate(lines):
        if line.lower() == value:
            return index
    return None


def find_date_indexes(lines):
    return [
        index
        for index, line in enumerate(lines)
        if re.search(r'\b\d{1,2}\.\s*\d{1,2}\.\s*20\d{2}\s*\|\s*\d{1,2}:\d{2}\b', line)
    ]


def extract_venue(lines, detail_date_index):
    if detail_date_index is None:
        return None

    for line in lines[detail_date_index + 1:]:
        if line.startswith('VSTUPENKY'):
            return None
        if line:
            return line

    return None


def infer_city(venue):
    if not venue:
        return None

    if '(PL)' in venue:
        return 'Gliwice'
    if 'Vídeň' in venue:
        return 'Vídeň'
    if venue == 'Zámek Dobrohoř':
        return 'Dobrohoř'
    if venue == 'Blovice, Hradiště':
        return 'Blovice'
    if any(district in venue for district in PRAGUE_DISTRICTS):
        return 'Praha'

    venue = re.sub(r'\([^)]*\)', '', venue)
    parts = [clean_text(part) for part in venue.split(',') if clean_text(part)]
    if len(parts) > 1:
        return parts[-1]

    return parts[0] if parts else None


def trim_footer(lines):
    trimmed = []
    for line in lines:
        normalized = line.lower().strip()
        if normalized in FOOTER_STARTS:
            break
        trimmed.append(line)
    return trimmed


def extract_description(lines, title, date_text, venue):
    start = first_index(lines, title)
    if start is None:
        start = 0

    content_lines = trim_footer(lines[start:])
    filtered = []
    for line in content_lines:
        if line in {'Skip to content', 'CZ', 'EN', 'O nás', 'Koncerty', 'Programy', 'Projekty', 'Média', 'Kontakt', 'Jak nás podpořit'}:
            continue
        filtered.append(line)

    if title and (not filtered or filtered[0].lower() != title.lower()):
        filtered.insert(0, title)
    if date_text and date_text not in filtered:
        filtered.insert(1, date_text)
    if venue and venue not in filtered:
        filtered.insert(2, venue)

    description = clean_text('\n'.join(filtered))
    return description or None


def extract_concert(session, url):
    soup = get_soup(session, url)
    lines = extract_content_lines(soup)
    text = '\n'.join(lines)

    title = lines[0] if lines else ''
    date, time_from = parse_datetime(text)
    if not title or not date:
        return None

    date_indexes = find_date_indexes(lines)
    detail_date_index = date_indexes[1] if len(date_indexes) > 1 else date_indexes[0] if date_indexes else None
    date_text = lines[detail_date_index] if detail_date_index is not None else None
    venue = extract_venue(lines, detail_date_index)

    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': venue,
        'city': infer_city(venue),
        'description': extract_description(lines, title, date_text, venue),
        'type': 'concert',
    }


def get_concerts():
    session = requests.Session()
    concert_links = find_concert_links(session)
    concerts = []

    for link in concert_links:
        concert = extract_concert(session, link)
        if concert:
            concerts.append(concert)

    return concerts


class CollegiumMarianumCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='collegiummarianum_cz',
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
    CollegiumMarianumCrawler().run()


if __name__ == '__main__':
    main()
