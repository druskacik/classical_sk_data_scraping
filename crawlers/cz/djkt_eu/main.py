import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.djkt.eu'
PROGRAM_URL = f'{BASE_URL}/program'
SOURCE = 'Divadlo J. K. Tyla'
CLASSICAL_ENSEMBLES = {'ensemble-opera', 'ensemble-balet'}
CONCERT_WORD = re.compile(r'\b(koncert|recitál|matiné)\b', re.IGNORECASE)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def clean_text(value):
    if not value:
        return ''
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_time(value):
    match = re.search(r'\b(\d{1,2}):(\d{2})', clean_text(value))
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def is_classical(row, title):
    classes = set(row.get('class') or [])
    return bool(classes & CLASSICAL_ENSEMBLES) or bool(CONCERT_WORD.search(title))


def extract_listing(html):
    soup = BeautifulSoup(html, 'html.parser')
    today = date.today()
    concerts = {}

    for month_header in soup.select('.list-head[id^="month-"]'):
        match = re.fullmatch(r'month-(\d{4})-(\d{2})', month_header.get('id', ''))
        if not match:
            continue
        year, month = map(int, match.groups())
        body = month_header.find_next_sibling('div', class_='list-body')
        if not body:
            continue

        for row in body.select(':scope > .row'):
            link = row.select_one('.col-event .title h3 a[href]')
            if not link:
                continue
            title = clean_text(link.get_text(' ', strip=True))
            if not title or not is_classical(row, title):
                continue

            day_node = row.select_one('.col-date .date')
            day_match = re.match(r'(\d{1,2})\.', clean_text(day_node.get_text() if day_node else ''))
            if not day_match:
                continue
            try:
                event_date = date(year, month, int(day_match.group(1)))
            except ValueError:
                continue
            if event_date < today:
                continue

            event_node = row.select_one('.col-event')
            venue_node = event_node.find('strong') if event_node else None
            venue = clean_text(venue_node.get_text(' ', strip=True) if venue_node else '')
            time_from = parse_time(event_node.get_text(' ', strip=True) if event_node else '')
            url = urljoin(BASE_URL, link.get('href'))

            title_node = row.select_one('.col-event .title')
            listing_description = clean_text(
                title_node.get_text('\n', strip=True) if title_node else title
            )
            key = (title, event_date.isoformat(), time_from, venue)
            concerts[key] = {
                'title': title,
                'date': event_date.isoformat(),
                'url': url,
                'time_from': time_from,
                'venue': venue or None,
                'city': 'Plzeň',
                'country_code': 'CZ',
                'description': listing_description or None,
                'source_url': BASE_URL,
                'source': SOURCE,
            }

    return concerts


def extract_detail(session, url, fallback):
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f'Failed to scrape detail {url}: {exc}')
        return fallback

    soup = BeautifulSoup(response.text, 'html.parser')
    parts = []

    # The main block contains the work/composer, synopsis, and production facts.
    main_block = soup.select_one('main .block-main')
    if main_block:
        parts.append(clean_text(main_block.get_text('\n', strip=True)))

    # Creator and cast blocks carry composer-relevant programme context and
    # performer information. Exclude generic gallery/review/page furniture.
    for block in soup.select('main .block-title'):
        heading = block.select_one('.heading, h2, h3')
        heading_text = clean_text(heading.get_text(' ', strip=True) if heading else '')
        if heading_text.upper() not in {'TVŮRCI', 'OSOBY A OBSAZENÍ'}:
            continue
        value = clean_text(block.get_text('\n', strip=True))
        if value:
            parts.append(value)

    description = clean_text('\n\n'.join(part for part in parts if part))
    return description or fallback


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(PROGRAM_URL, timeout=30)
    response.raise_for_status()

    concerts = extract_listing(response.text)
    details = {}
    unique_urls = {
        concert['url']: concert['description'] for concert in concerts.values()
    }
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(extract_detail, session, url, fallback): url
            for url, fallback in unique_urls.items()
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                details[url] = future.result()
            except Exception as exc:
                print(f'Failed to process detail {url}: {exc}')

    for concert in concerts.values():
        concert['description'] = details.get(
            concert['url'], concert['description']
        )

    return sorted(
        concerts.values(),
        key=lambda item: (item['date'], item['time_from'] or '', item['title']),
    )


class DjktCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='djkt_eu',
        source=SOURCE,
        source_url=BASE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'venue',
            'city',
            'country_code',
            'description',
            'source_url',
            'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    DjktCrawler().run()


if __name__ == '__main__':
    main()
