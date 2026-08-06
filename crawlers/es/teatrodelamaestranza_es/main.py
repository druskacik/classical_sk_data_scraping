import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.teatrodelamaestranza.es/es/'
PROGRAM_URL = urljoin(SOURCE_URL, 'programacion/')
SOURCE = 'Teatro de la Maestranza'
CITY = 'Sevilla'
DEFAULT_VENUE = 'Teatro de la Maestranza'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.7',
}

MONTHS = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'setiembre': 9, 'octubre': 10,
    'noviembre': 11, 'diciembre': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_schedule(schedule, default_year, default_month):
    """Expand the site's Spanish date lists into individual performances."""
    schedule = clean_text(schedule).lower().replace('hs.', 'h.').replace('h y ', 'h. y ')
    schedule = re.sub(r'\s+', ' ', schedule)
    results = []
    pattern = re.compile(
        r'((?:\d{1,2}\s*(?:,|y)?\s*)+)de\s+([a-záéíóúñ]+)\s*,?\s*'
        r'((?:[012]?\d:[0-5]\d\s*h\.?\s*(?:y\s*)?)*)',
    )
    matches = list(pattern.finditer(schedule))
    # A defensive fallback covers a date with no written month.
    if not matches:
        matches = [re.match(r'((?:\d{1,2}\s*)+)(.*)', schedule)]
    for match in matches:
        month = MONTHS.get(match.group(2)) if match.lastindex >= 2 else default_month
        if not month:
            continue
        text = match.group(0)
        year_match = re.search(r'\b(20\d{2})\b', text)
        year = int(year_match.group(1)) if year_match else default_year
        days = [int(value) for value in re.findall(r'\b(\d{1,2})\b', match.group(1))]
        times = re.findall(r'\b([012]?\d):([0-5]\d)\s*h', text)
        if not times:
            times = [(None, None)]
        for day in days:
            for hour, minute in times:
                try:
                    event_date = date(year, month, day).isoformat()
                except ValueError:
                    continue
                results.append((event_date, f'{int(hour):02d}:{minute}' if hour else None))
    return results


def detail_data(session, url):
    soup = get_soup(session, url)
    room = ''
    for item in soup.select('.contentTitle li'):
        if clean_text(item).lower().startswith('sala:'):
            room = re.sub(r'^sala:\s*', '', clean_text(item), flags=re.I)
            break
    venue = DEFAULT_VENUE
    if room and room.lower() not in venue.lower():
        venue = f'{venue} – {room}'

    parts = []
    for selector in ('.descriptionTags', '.secondTtf'):
        text = clean_text(soup.select_one(selector))
        if text and text not in parts:
            parts.append(text)
    return venue, clean_text('\n\n'.join(parts)) or None


def listing_items(session):
    soup = get_soup(session, PROGRAM_URL)
    items = []
    for group in soup.select('.items-programme'):
        heading = clean_text(group.select_one('.month'))
        match = re.search(r'([A-Za-záéíóúñ]+)\s+(20\d{2})', heading, re.I)
        if not match:
            continue
        default_month = MONTHS.get(match.group(1).lower())
        default_year = int(match.group(2))
        for card in group.select('.item'):
            link = card.select_one('h3 a[href*="/shows/detalle/"]')
            title = clean_text(link)
            subtitle = clean_text(card.select_one('h4'))
            if subtitle and subtitle.lower() not in title.lower():
                title = f'{title} – {subtitle}'
            schedule = card.select_one('.info__schedule')
            type_node = schedule.select_one('.show-type') if schedule else None
            if type_node:
                type_node.extract()
            url = urljoin(SOURCE_URL, link.get('href')) if link else ''
            performances = parse_schedule(schedule, default_year, default_month)
            if title and url and performances:
                items.append((title, url, performances))
    return items


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = listing_items(session)
    details = {}
    urls = {item[1] for item in items}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(detail_data, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                details[url] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed', level='warning', url=url,
                    error_type=type(error).__name__, error_message=str(error),
                )
                details[url] = (DEFAULT_VENUE, None)

    records = []
    for title, url, performances in items:
        venue, description = details[url]
        for event_date, time_from in performances:
            records.append({
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': CITY,
                'country_code': 'ES',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    unique = {(r['title'], r['date'], r['time_from'], r['venue']): r for r in records}
    return sorted(unique.values(), key=lambda r: (r['date'], r['time_from'] or '', r['title']))


class TeatroDeLaMaestranzaEsCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='teatrodelamaestranza_es',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='ES',
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
    TeatroDeLaMaestranzaEsCrawler().run()


if __name__ == '__main__':
    main()
