import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.ndm.cz'
PROGRAM_URL = f'{BASE_URL}/cz/program/aktualni-mesic/'
SOURCE = 'Národní divadlo moravskoslezské'
CLASSICAL_TYPES = ('opera', 'balet', 'koncert')

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
    text = unescape(str(value)).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_time(value):
    match = re.search(r'\b(\d{1,2}):(\d{2})\b', clean_text(value))
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def date_from_url(url):
    match = re.search(r'/(\d{4}-\d{2}-\d{2})/\d+/?(?:[?#].*)?$', url)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def extract_listing(soup):
    today = date.today()
    concerts = {}

    # The desktop calendar has one column per venue. The site also renders a
    # mobile copy of the same events, so the final dictionary removes repeats.
    for row in soup.select('.program-row'):
        columns = row.find_all('div', class_='program-col', recursive=False)
        if len(columns) < 2:
            continue

        header = row.find_parent().find_previous_sibling(
            'div', class_='program-header'
        )
        venues = []
        if header:
            venues = [
                clean_text(node.get_text(' ', strip=True))
                for node in header.select('.program-header-col > span')
            ]

        for column_index, column in enumerate(columns[1:]):
            fallback_venue = (
                venues[column_index]
                if column_index < len(venues)
                else None
            )
            for item in column.select('.program-item'):
                genre_node = item.select_one('.program-item-type')
                genre = clean_text(
                    genre_node.get_text(' ', strip=True) if genre_node else ''
                ).lower()
                if not any(kind in genre for kind in CLASSICAL_TYPES):
                    continue

                link = item.select_one('.program-item-title a[href]')
                if not link:
                    continue
                url = urljoin(BASE_URL, link.get('href'))
                event_date = date_from_url(url)
                if not event_date or event_date < today:
                    continue

                title = clean_text(
                    link.get('title') or link.get_text(' ', strip=True)
                )
                if not title:
                    continue
                time_node = item.select_one('.program-item-time')
                author_node = item.select_one('.program-item-author')
                time_from = parse_time(
                    time_node.get_text(' ', strip=True) if time_node else ''
                )
                author = clean_text(
                    author_node.get_text(' ', strip=True)
                    if author_node
                    else ''
                )
                key = (title, event_date.isoformat(), time_from, url)
                concerts[key] = {
                    'title': title,
                    'date': event_date.isoformat(),
                    'url': url,
                    'time_from': time_from,
                    'venue': fallback_venue,
                    'city': 'Ostrava',
                    'country_code': 'CZ',
                    'description': author or None,
                    'source_url': BASE_URL,
                    'source': SOURCE,
                }

    return concerts


def extract_detail(session, url, fallback_description, fallback_venue):
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return fallback_description, fallback_venue

    soup = BeautifulSoup(response.text, 'html.parser')
    tabs = soup.select_one('.tabs')
    if not tabs:
        return fallback_description, fallback_venue

    description_parts = []
    author = tabs.select_one('.detail-author')
    summary_nodes = tabs.select('.detail-info')
    content = tabs.select_one('.tab.active .tab-col-content')

    if author:
        description_parts.append(clean_text(author.get_text(' ', strip=True)))
    for node in summary_nodes:
        value = clean_text(node.get_text('\n', strip=True))
        if value and value not in description_parts:
            description_parts.append(value)
    if content:
        for node in content.select('script, style, form, img'):
            node.decompose()
        value = clean_text(content.get_text('\n', strip=True))
        if value:
            description_parts.append(value)

    venue = fallback_venue
    for node in summary_nodes:
        text = clean_text(node.get_text(' ', strip=True))
        match = re.search(r'\d{1,2}:\d{2}\s*-\s*(.+)$', text)
        if match:
            venue = clean_text(match.group(1))
            break

    description = clean_text('\n\n'.join(description_parts))
    return description or fallback_description, venue


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(PROGRAM_URL, timeout=30)
    response.raise_for_status()

    concerts = extract_listing(BeautifulSoup(response.text, 'html.parser'))
    details = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                extract_detail,
                session,
                concert['url'],
                concert['description'],
                concert['venue'],
            ): concert['url']
            for concert in concerts.values()
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                details[url] = future.result()
            except requests.RequestException:
                continue

    for concert in concerts.values():
        detail = details.get(concert['url'])
        if detail:
            concert['description'], concert['venue'] = detail

    return sorted(
        concerts.values(),
        key=lambda item: (item['date'], item['time_from'] or '', item['title']),
    )


class NdmCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ndm_cz',
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
    concerts = NdmCrawler().scrape()
    print(f'Found {len(concerts)} concerts')
    for concert in concerts:
        print(concert)


if __name__ == '__main__':
    main()
