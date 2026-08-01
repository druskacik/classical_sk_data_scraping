import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.jihoceskedivadlo.cz'
SOURCE_URL = f'{BASE_URL}/'
SOURCE = 'Jihočeské divadlo'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36',
}


def clean_text(value):
    text = unescape(value or '').replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def city_from_venue(venue):
    if not venue:
        return None
    if 'České Budějovice' in venue or 'Budova JD' in venue:
        return 'České Budějovice'
    if 'Český Krumlov' in venue or 'Otáčivé hlediště' in venue:
        return 'Český Krumlov'
    if 'Třeboň' in venue:
        return 'Třeboň'
    if 'Týn nad Vltavou' in venue:
        return 'Týn nad Vltavou'
    if 'Holašovice' in venue:
        return 'Holašovice'
    return None


def parse_performance_date(modal):
    cast = modal.select_one('a[href*="?cast="]')
    if cast:
        match = re.search(r'cast=(\d{4}-\d{2}-\d{2})-', cast.get('href', ''))
        if match:
            return match.group(1)
    time_node = modal.select_one('.time')
    match = re.search(r'\b(\d{1,2})\.(\d{1,2})\.', clean_text(time_node.get_text(' ', strip=True) if time_node else ''))
    if not match:
        return None
    year = date.today().year
    month, day = map(int, match.groups()[::-1])
    if month < date.today().month - 6:
        year += 1
    return f'{year:04d}-{month:02d}-{day:02d}'


def parse_time(modal):
    node = modal.select_one('.time')
    match = re.search(r'\b\d{1,2}\.\d{1,2}\.\s+(\d{1,2}):(\d{2})\b', clean_text(node.get_text(' ', strip=True) if node else ''))
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def extract_listing(soup):
    records = {}
    for modal in soup.select('.min_calendar_modal'):
        title_node = modal.select_one('.min_calendar_modal-name')
        detail = modal.select_one('a[href*="/porad/"]')
        if not title_node or not detail:
            continue
        event_date = parse_performance_date(modal)
        if not event_date or event_date < date.today().isoformat():
            continue
        title = clean_text(title_node.get_text(' ', strip=True))
        url = urljoin(BASE_URL, detail.get('href'))
        venue_node = modal.select_one('.location')
        venue = clean_text(venue_node.get_text(' ', strip=True)) if venue_node else None
        description_node = modal.select_one('.min_calendar_modal-description')
        description = clean_text(description_node.get_text('\n', strip=True)) if description_node else None
        key = (url, event_date, parse_time(modal))
        records[key] = {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': parse_time(modal),
            'venue': venue,
            'city': city_from_venue(venue),
            'country_code': 'CZ',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
    return records


def extract_detail(session, record):
    try:
        response = session.get(record['url'], headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return record
    soup = BeautifulSoup(response.text, 'html.parser')
    section = soup.select_one('#banner_detail-popis')
    if section:
        for node in section.select('script, style, img, form'):
            node.decompose()
        detail_text = clean_text(section.get_text('\n', strip=True))
        if detail_text:
            record['description'] = detail_text
    return record


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(SOURCE_URL, timeout=30)
    response.raise_for_status()
    records = list(extract_listing(BeautifulSoup(response.text, 'html.parser')).values())
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(extract_detail, session, record) for record in records]
        records = [future.result() for future in as_completed(futures)]
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class JihoceskeDivadloCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='jihoceskedivadlo_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'url', 'time_from', 'venue', 'city', 'country_code', 'description', 'source_url', 'source'],
        dedupe_subset=['title', 'date', 'url', 'time_from'],
    )

    def scrape(self):
        return get_concerts()


def main():
    JihoceskeDivadloCrawler().run()


if __name__ == '__main__':
    main()
