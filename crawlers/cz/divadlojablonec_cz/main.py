import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.divadlojablonec.cz/'
PROGRAM_URL = urljoin(SOURCE_URL, 'program')
ARCHIVE_URL = urljoin(SOURCE_URL, 'archiv-predstaveni')
SOURCE = 'Městské divadlo Jablonec nad Nisou'
HOME_CITY = 'Jablonec nad Nisou'
HOME_VENUE = 'Městské divadlo Jablonec nad Nisou'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'cs-CZ,cs;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    value = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def parse_date(value):
    match = re.search(r'(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})(?!\d)', value or '')
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\bod\s+(\d{1,2})(?::(\d{2}))?\s*hodin', value or '', re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def current_items(soup):
    items = []
    for node in soup.select('.program-items .program-item'):
        link = node.select_one('a[href]')
        title_node = node.select_one('.caption-info strong')
        date_node = node.select_one('.event-date')
        if not link or not title_node or not date_node:
            continue
        title = clean_text(title_node.get_text(' ', strip=True))
        date_text = clean_text(date_node.get_text(' ', strip=True))
        event_date = parse_date(date_text)
        if title and event_date:
            items.append({
                'title': title,
                'date': event_date,
                'time_from': parse_time(date_text),
                'url': urljoin(SOURCE_URL, link.get('href')),
            })
    return items


def archive_year_urls(soup):
    urls = set()
    for link in soup.select('a[href*="archiv-predstaveni?y="]'):
        if re.search(r'[?&]y=\d{4}(?:&|$)', link.get('href', '')):
            urls.add(urljoin(SOURCE_URL, link.get('href')))
    return sorted(urls)


def archive_items(soup):
    items = []
    for row in soup.select('tr .program-title a[href]'):
        container = row.find_parent('tr')
        date_node = container.select_one('td.program-date span.program-date') if container else None
        title = clean_text(row.get_text(' ', strip=True))
        event_date = parse_date(date_node.get_text(' ', strip=True) if date_node else '')
        if title and event_date:
            items.append({
                'title': title,
                'date': event_date,
                'time_from': None,
                'url': urljoin(SOURCE_URL, row.get('href')),
            })
    return items


def infer_location(description):
    text = clean_text(description)
    # The calendar is venue-specific, but it also advertises a small number of
    # explicitly off-site performances. Preserve the named local venue when the
    # detail text makes that exception clear.
    patterns = (
        r'(?:uskuteční|koná|proběhne)\s+(?:se\s+)?v\s+((?:kostele|kapli|kině|galerii|sále)\s+[^.\n]+)',
        r'místo(?: konání)?\s*:\s*([^.\n]+)',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        venue = clean_text(match.group(1)).rstrip(' .')
        venue = re.split(r'\s+(?:na adrese|v Jablonci nad Nisou)\b', venue, maxsplit=1, flags=re.IGNORECASE)[0]
        venue = re.sub(
            r'^(kostele|kapli|kině|galerii|sále)\b',
            lambda found: {
                'kostele': 'Kostel', 'kapli': 'Kaple', 'kině': 'Kino',
                'galerii': 'Galerie', 'sále': 'Sál',
            }[found.group(1).lower()],
            venue,
            flags=re.IGNORECASE,
        )
        local_evidence = re.search(
            r'Jablonc|Junior|Eurocentr|Nejsvětějšího srdce|svaté Anny|Dr Farského',
            match.group(0),
            re.IGNORECASE,
        )
        if venue and len(venue) <= 160 and local_evidence:
            return venue[0].upper() + venue[1:], HOME_CITY
    return HOME_VENUE, HOME_CITY


def enrich_item(session, item):
    soup = get_soup(session, item['url'])
    subtitle = soup.select_one('.event-subtitle')
    subtitle_text = clean_text(subtitle.get_text(' ', strip=True)) if subtitle else ''
    description_node = soup.select_one('.event-description')
    if description_node:
        for unwanted in description_node.select('script, style, form, img'):
            unwanted.decompose()
        description = clean_text(description_node.get_text('\n', strip=True)) or None
    else:
        description = None
    venue, city = infer_location(description)
    return {
        'title': item['title'],
        'date': item['date'],
        'url': item['url'],
        'time_from': parse_time(subtitle_text) or item['time_from'],
        'venue': venue,
        'city': city,
        'country_code': 'CZ',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def fallback_record(item):
    return {
        'title': item['title'],
        'date': item['date'],
        'url': item['url'],
        'time_from': item['time_from'],
        'venue': HOME_VENUE,
        'city': HOME_CITY,
        'country_code': 'CZ',
        'description': None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    program_soup = get_soup(session, PROGRAM_URL)
    archive_soup = get_soup(session, ARCHIVE_URL)
    items = current_items(program_soup) + archive_items(archive_soup)

    year_urls = archive_year_urls(archive_soup)
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_urls = {executor.submit(get_soup, session, url): url for url in year_urls}
        for future in as_completed(future_urls):
            url = future_urls[future]
            try:
                items.extend(archive_items(future.result()))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape archive year', event='crawler_page_failed', level='warning',
                    url=url, error_type=type(error).__name__, error_message=str(error),
                )

    unique_items = {}
    for item in items:
        key = (item['title'], item['date'], item['url'])
        existing = unique_items.get(key)
        if not existing or (item['time_from'] and not existing['time_from']):
            unique_items[key] = item

    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(enrich_item, session, item): item
            for item in unique_items.values()
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                records.append(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape event detail', event='crawler_item_failed', level='warning',
                    url=item['url'], error_type=type(error).__name__, error_message=str(error),
                )

                records.append(fallback_record(item))

    deduplicated = {}
    for record in records:
        key = (record['title'], record['date'], record['time_from'], record['venue'])
        existing = deduplicated.get(key)
        if not existing or (record['description'] and not existing['description']):
            deduplicated[key] = record

    return sorted(deduplicated.values(), key=lambda record: (
        record['date'], record['time_from'] or '', record['title'], record['url']
    ))


class DivadlojablonecCzCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='divadlojablonec_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    DivadlojablonecCzCrawler().run()


if __name__ == '__main__':
    main()
