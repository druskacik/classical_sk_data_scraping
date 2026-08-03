import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


BASE_URL = 'https://www.filharmonie-zlin.cz'
SOURCE_URL = f'{BASE_URL}/'
SOURCE = 'Filharmonie Bohuslava Martinů'
START_YEAR = 2021
FUTURE_YEARS = 2

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'cs,en;q=0.8',
}

CITY_NAMES = (
    'Uherské Hradiště', 'Český Krumlov', 'Rožnov pod Radhoštěm',
    'Valašské Meziříčí', 'Frýdek-Místek', 'Nové Město na Moravě',
    'Luhačovice', 'Kroměříž', 'Holešov', 'Velehrad', 'Vizovice',
    'Otrokovice', 'Uherský Brod', 'Zlín', 'Přerov', 'Brno', 'Praha',
    'Olomouc', 'Ostrava', 'Vsetín', 'Krems', 'Vídeň', 'Wien',
)

LOCAL_VENUE_MARKERS = (
    'kongresové centrum', 'kongresové centrum zlín', 'velké kino',
    'alternativa', 'kostel sv. filipa a jakuba', '14|15 baťův institut',
    '14/15 baťův institut',
    'park komenského', 'zlínský zámek',
)


def clean_text(value):
    if not value:
        return ''
    value = value.replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[ \t\r\f\v]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_date(value):
    match = re.search(r'(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})', value or '')
    if not match:
        return None
    day, month, year = map(int, match.groups())
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2})[.:](\d{2})\b', value or '')
    if not match:
        return None
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def infer_city(venue):
    normalized = clean_text(venue)
    for city in CITY_NAMES:
        if re.search(rf'(?<!\w){re.escape(city)}(?!\w)', normalized, re.IGNORECASE):
            return 'Vídeň' if city == 'Wien' else city

    lowered = normalized.lower()
    if any(marker in lowered for marker in LOCAL_VENUE_MARKERS):
        return 'Zlín'
    return None


def calendar_url(year, month):
    # The site accepts only previous/next-month actions. Asking for the month
    # after the target and moving back produces a stable URL for any archive month.
    next_year, next_month = year, month + 1
    if next_month == 13:
        next_year, next_month = year + 1, 1
    return f'{BASE_URL}/kalendar?calact=bm&date={next_year}-{next_month}'


def iter_months():
    today = date.today()
    for year in range(START_YEAR, today.year + FUTURE_YEARS + 1):
        for month in range(1, 13):
            yield year, month


def discover_urls(session):
    seen = set()
    for year, month in iter_months():
        url = calendar_url(year, month)
        try:
            soup = get_soup(session, url)
        except requests.RequestException as exc:
            log_message(
                'Failed to scrape calendar month', event='crawler_page_failed',
                level='warning', url=url, error_type=type(exc).__name__,
                error_message=str(exc),
            )
            continue

        for link in soup.select('.cal-detail .tooltip-right h3 a[href]'):
            event_url = urljoin(BASE_URL, link.get('href'))
            if event_url not in seen:
                seen.add(event_url)
                yield event_url


def extract_detail(session, url):
    soup = get_soup(session, url)
    title_el = soup.select_one('h1.event-detail__title')
    date_el = soup.select_one('.event-detail__date')
    venue_el = soup.select_one('.event-detail__venue')
    description_el = soup.select_one('.event-detail__perex')

    title = clean_text(title_el.get_text(' ', strip=True)) if title_el else None
    date_text = clean_text(date_el.get_text(' ', strip=True)) if date_el else None
    venue = clean_text(venue_el.get_text(' ', strip=True)) if venue_el else None
    if venue:
        venue = re.sub(r'^Místo konání:\s*', '', venue, flags=re.IGNORECASE)
        venue = venue.rstrip(' |')
    description = (
        clean_text(description_el.get_text('\n', strip=True)) or None
        if description_el else None
    )

    return {
        'title': title,
        'date': parse_date(date_text),
        'time_from': parse_time(date_text),
        'venue': venue,
        'city': infer_city(venue),
        'description': description,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    concerts = []

    for url in discover_urls(session):
        try:
            detail = extract_detail(session, url)
        except requests.RequestException as exc:
            log_message(
                'Failed to scrape concert detail', event='crawler_item_failed',
                level='warning', url=url, error_type=type(exc).__name__,
                error_message=str(exc),
            )
            continue

        if not all((detail['title'], detail['date'], detail['venue'], detail['city'])):
            log_message(
                'Skipping event with missing required details',
                event='crawler_item_skipped', level='warning', url=url,
            )
            continue

        concerts.append({
            'title': detail['title'],
            'date': detail['date'],
            'url': url,
            'time_from': detail['time_from'],
            'venue': detail['venue'],
            'city': detail['city'],
            'country_code': 'CZ',
            'description': detail['description'],
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })

    return concerts


class FilharmonieZlinCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='filharmonie_zlin_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        upload_target='classical',
        dedupe_subset=['title', 'date', 'time_from', 'url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    FilharmonieZlinCrawler().run()


if __name__ == '__main__':
    main()
