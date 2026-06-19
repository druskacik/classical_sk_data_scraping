import re
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.varhannifestival.cz'
SOURCE_NAME = 'Svatovítské varhanní večery'
SOURCE_URL = BASE_URL
DEFAULT_CITY = 'Praha'
DEFAULT_VENUE = 'Katedrála sv. Víta, Václava a Vojtěcha'
DEFAULT_TIME = '19:00'

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


def get_program_year(soup):
    text = soup.get_text('\n', strip=True)
    match = re.search(r'30/6\s*[–-]\s*1/9\s+(20\d{2})', text)
    if match:
        return int(match.group(1))

    og_site_name = soup.select_one('meta[property="og:site_name"]')
    if og_site_name:
        match = re.search(r'(20\d{2})', og_site_name.get('content', ''))
        if match:
            return int(match.group(1))

    return None


def parse_date(summary, year):
    match = re.search(r'\b(\d{1,2})/(\d{1,2})\b', summary)
    if not match or not year:
        return None
    day = int(match.group(1))
    month = int(match.group(2))
    return f'{year}-{month:02d}-{day:02d}'


def parse_title(summary):
    lines = [line.strip() for line in clean_text(summary).split('\n') if line.strip()]
    if lines and re.fullmatch(r'\d{1,2}/\d{1,2}', lines[0]):
        lines = lines[1:]

    title_lines = []
    for line in lines:
        if line.startswith('–') or line.startswith('-'):
            break
        if re.fullmatch(r'\([^)]+\)', line):
            break
        title_lines.append(line)

    title = clean_text(' '.join(title_lines))
    return title or SOURCE_NAME


def get_program_rows(soup):
    program_section = soup.select_one('#program')
    if not program_section:
        return []

    rows = program_section.select(':scope > .section-body > .row-main > .col:nth-of-type(2) > .row')
    if rows:
        return rows

    return program_section.select('.row')


def extract_concerts(soup):
    year = get_program_year(soup)
    rows = get_program_rows(soup)
    concerts = []

    for index, row in enumerate(rows):
        summary_el = row.select_one('.block-inline p.text-large')
        if not summary_el:
            continue

        summary = clean_text(summary_el.get_text('\n', strip=True))
        if not re.search(r'\b\d{1,2}/\d{1,2}\b', summary):
            continue

        accordion_el = None
        for next_row in rows[index + 1:]:
            accordion_el = next_row.select_one('.accordion__content')
            if accordion_el:
                break
            if next_row.select_one('.block-inline p.text-large'):
                break

        detail_program = clean_text(accordion_el.get_text('\n', strip=True)) if accordion_el else ''
        description = clean_text('\n\n'.join(part for part in [summary, detail_program] if part)) or None
        ticket_link = row.select_one('a.btn[href]')
        url = urljoin(BASE_URL, ticket_link.get('href')) if ticket_link else f'{BASE_URL}/#program'

        concerts.append({
            'title': parse_title(summary),
            'date': parse_date(summary, year),
            'url': url,
            'time_from': DEFAULT_TIME,
            'time_to': None,
            'venue': DEFAULT_VENUE,
            'city': DEFAULT_CITY,
            'description': description,
            'type': 'concert',
        })

    return [concert for concert in concerts if concert['title'] and concert['date']]


def get_concerts():
    session = requests.Session()
    soup = get_soup(session, BASE_URL)
    return extract_concerts(soup)


class VarhanniFestivalCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='varhannifestival_cz',
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
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    VarhanniFestivalCrawler().run()


if __name__ == '__main__':
    main()
