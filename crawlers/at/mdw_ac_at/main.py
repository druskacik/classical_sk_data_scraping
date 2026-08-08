import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.mdw.ac.at/'
CALENDAR_URL = urljoin(SOURCE_URL, '6/')
SOURCE = 'mdw – Universität für Musik und darstellende Kunst Wien'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'jan': 1, 'jän': 1, 'feb': 2, 'mär': 3, 'mar': 3, 'apr': 4,
    'mai': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'okt': 10,
    'nov': 11, 'dez': 12,
}


def clean_text(element):
    if element is None:
        return ''
    text = element.get_text('\n', strip=True) if hasattr(element, 'get_text') else str(element)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def canonical_event_url(url):
    parts = urlsplit(urljoin(SOURCE_URL, url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))


def listing_urls(session):
    # Supplying a deliberately early start asks the calendar for every event it
    # still exposes. At present the backend retains only its upcoming catalogue.
    end_year = date.today().year + 2
    response = session.get(
        CALENDAR_URL,
        params={'daterange': f'01.01.2000 – 31.12.{end_year}'},
        timeout=60,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    return sorted({
        canonical_event_url(link['href'])
        for link in soup.select('.verMain a[href*="v="][href]')
    })


def parse_datetime(value):
    match = re.search(
        r'(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\.?\s*(20\d{2})'
        r'(?:,\s*(\d{1,2}):(\d{2}))?',
        value,
    )
    if not match:
        return None, None
    month = MONTHS.get(match.group(2).lower()[:3])
    if not month:
        return None, None
    try:
        event_date = date(int(match.group(3)), month, int(match.group(1))).isoformat()
    except ValueError:
        return None, None
    event_time = None
    if match.group(4) is not None:
        hour, minute = int(match.group(4)), int(match.group(5))
        if hour < 24 and minute < 60:
            event_time = f'{hour:02d}:{minute:02d}'
    return event_date, event_time


def parse_location(lines):
    if len(lines) < 2:
        return None
    venue = lines[0]
    location = ' '.join(lines[1:])
    city_match = re.search(r'\b\d{4}\s+([^,]+?)(?:,\s*(?:Österreich|Austria))?$', location)
    if not city_match:
        return None
    city = city_match.group(1).strip()
    if not venue or not city:
        return None
    return venue, city


def parse_detail(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    event = soup.select_one('article.content #Veranstaltung')
    title = clean_text(event.select_one('h1')) if event else ''
    aside = soup.select_one('article.content aside')
    place = aside.select_one('p') if aside else None
    lines = [line for line in clean_text(place).splitlines() if line]
    if lines and lines[0].lower().startswith('zeit & ort'):
        lines.pop(0)
    if not title or not lines:
        return None

    event_date, event_time = parse_datetime(lines.pop(0))
    location = parse_location(lines)
    if not event_date or not location:
        return None
    venue, city = location

    description_parts = []
    for child in event.find_all(['p', 'h5'], recursive=False):
        value = clean_text(child)
        if value and value not in description_parts:
            description_parts.append(value)

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': event_time,
        'venue': venue,
        'city': city,
        'country_code': 'AT',
        'description': clean_text('\n\n'.join(description_parts)) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class MdwAcAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mdw_ac_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        urls = listing_urls(session)
        records = []

        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(session.get, url, timeout=45): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    response = future.result()
                    response.raise_for_status()
                    record = parse_detail(response.text, url)
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch mdw event detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                    continue
                if record:
                    records.append(record)

        return sorted(records, key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['url']
        ))


def main():
    MdwAcAtCrawler().run()


if __name__ == '__main__':
    main()
