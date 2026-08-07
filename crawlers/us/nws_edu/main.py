import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.nws.edu/'
SOURCE = 'New World Symphony'
CONCERTS_PATH = '/events-tickets/concerts/'
API_URL = urljoin(SOURCE_URL, 'api/content/')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        'Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
}
DATE_RE = re.compile(
    r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+'
    r'([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\s+at\s+'
    r'(\d{1,2}:\d{2}\s+[AP]M)',
)


def clean_html(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_content(path):
    response = requests.get(
        API_URL,
        params={'path': path},
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    return response.json()


def resolve_city(venue, date_range=''):
    value = f'{venue}\n{date_range}'.casefold()
    if 'north miami' in value:
        return 'North Miami'
    if 'coral gables' in value:
        return 'Coral Gables'
    if 'miami beach' in value:
        return 'Miami Beach'
    if 'miami' in value or 'overtown' in value or 'adrienne arsht' in value:
        return 'Miami'
    if (
        'new world center' in value
        or 'soundscape park' in value
        or 'the fillmore miami beach' in value
    ):
        return 'Miami Beach'
    return None


def description_from(properties):
    parts = []
    tabs = properties.get('tabs') or {}
    for item in tabs.get('items') or []:
        content = item.get('content') or {}
        tab_properties = content.get('properties') or {}
        title = clean_html(tab_properties.get('title'))

        text = clean_html(tab_properties.get('text'))
        if text:
            parts.append(f'{title}\n{text}' if title else text)

        repertoire = tab_properties.get('repertoire') or {}
        works = []
        for work_item in repertoire.get('items') or []:
            work = (work_item.get('content') or {}).get('properties') or {}
            composer = clean_html(work.get('composer'))
            piece = clean_html(work.get('fullPieceTitle') or work.get('shortName'))
            heading = ': '.join(value for value in (composer, piece) if value)
            movements = clean_html(work.get('movementTitles'))
            note = clean_html(work.get('programNote'))
            work_text = '\n'.join(value for value in (heading, movements, note) if value)
            if work_text:
                works.append(work_text)
        if works:
            parts.append(f'{title}\n' + '\n\n'.join(works) if title else '\n\n'.join(works))

    unique = []
    for part in parts:
        if part and part not in unique:
            unique.append(part)
    return '\n\n'.join(unique) or None


def detail_records(path):
    data = fetch_content(path)
    properties = data.get('properties') or {}
    title = clean_html(data.get('title'))
    venue = clean_html(properties.get('venue'))
    date_range = clean_html(properties.get('dateRange'))
    city = resolve_city(venue, date_range)
    if not title or not venue or not city:
        return []

    url = urljoin(SOURCE_URL, data.get('url') or path)
    description = description_from(properties)
    records = []
    for date_text, time_text in DATE_RE.findall(date_range):
        try:
            start = datetime.strptime(
                f'{date_text} {time_text}', '%B %d, %Y %I:%M %p'
            )
        except ValueError:
            continue
        records.append({
            'title': title,
            'date': start.date().isoformat(),
            'url': url,
            'time_from': start.strftime('%H:%M'),
            'venue': venue,
            'city': city,
            'country_code': 'US',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


class NwsEduCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='nws_edu',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        index = fetch_content(CONCERTS_PATH)
        paths = {
            item.get('url')
            for item in index.get('childNodes') or []
            if item.get('url') and item.get('contentType') == 'eventPage'
        }
        records = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(detail_records, path): path for path in paths}
            for future in as_completed(futures):
                path = futures[future]
                try:
                    records.extend(future.result())
                except (requests.RequestException, ValueError) as error:
                    log_message(
                        'Failed to scrape New World Symphony concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=urljoin(SOURCE_URL, path),
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(records, key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['venue']
        ))


def main():
    NwsEduCrawler().run()


if __name__ == '__main__':
    main()
