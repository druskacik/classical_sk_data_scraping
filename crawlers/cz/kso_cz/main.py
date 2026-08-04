import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.kso.cz/'
PROGRAM_URL = urljoin(SOURCE_URL, 'program')
SOURCE = 'Karlovarský symfonický orchestr'
HOME_CITY = 'Karlovy Vary'
ARCHIVE_START_YEAR = 2022

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'cs-CZ,cs;q=0.9,en;q=0.7',
}

MONTHS = {
    'leden': 1, 'ledna': 1, 'unor': 2, 'unora': 2, 'brezen': 3,
    'brezna': 3, 'duben': 4, 'dubna': 4, 'kveten': 5, 'kvetna': 5,
    'cerven': 6, 'cervna': 6, 'cervenec': 7, 'cervence': 7,
    'srpen': 8, 'srpna': 8, 'zari': 9, 'rijen': 10, 'rijna': 10,
    'listopad': 11, 'listopadu': 11, 'prosinec': 12, 'prosince': 12,
}

# Locations outside the orchestra's home city are explicitly marked in the
# venue field.  Sedlec and Rybáře are districts of Karlovy Vary.
TOUR_CITIES = {
    'praha': 'Praha',
    'prazskeho hradu': 'Praha',
    'prazsky hrad': 'Praha',
    'prazske konzervatore': 'Praha',
    'ostrov': 'Ostrov',
    'cheb': 'Cheb',
    'sokolov': 'Sokolov',
    'loket': 'Loket',
    'jihlava': 'Jihlava',
    'jachymov': 'Jáchymov',
    'hermanuv mestec': 'Heřmanův Městec',
    'podebrady': 'Poděbrady',
}


def clean_text(value):
    if not value:
        return ''
    value = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def fold(value):
    translation = str.maketrans(
        'áčďéěíňóřšťúůýž',
        'acdeeinorstuuyz',
    )
    return clean_text(value).lower().translate(translation)


def parse_date(value, year):
    match = re.search(r'(\d{1,2})\.\s*([A-Za-zÀ-ſ]+)', clean_text(value))
    if not match:
        return None
    month = MONTHS.get(fold(match.group(2)))
    if not month:
        return None
    try:
        return date(year, month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.fullmatch(r'\s*(\d{1,2}):(\d{2})\s*', value or '')
    if not match:
        return None
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def resolve_city(venue):
    normalized = fold(venue)
    for marker, city in TOUR_CITIES.items():
        if marker in normalized:
            return city
    return HOME_CITY


def event_description(modal):
    rows = modal.select('.modal-body > .row.no-gutters')
    if len(rows) < 2:
        return None
    content = rows[1]
    for unwanted in content.select('script, style, form, img, a'):
        unwanted.decompose()
    return clean_text(content.get_text('\n', strip=True)) or None


def parse_event(modal, year, page_url):
    title_node = modal.select_one('.modal-title')
    values = [
        clean_text(node.get_text(' ', strip=True))
        for node in modal.select('.modal-lineup td.pl-2.font-weight-bold')
    ]
    if not title_node or len(values) < 3:
        return None

    title = clean_text(title_node.get_text(' ', strip=True))
    event_date = parse_date(values[0], year)
    time_from = parse_time(values[1])
    venue = clean_text(values[2])
    event_id = modal.get('id')
    if not title or not event_date or not venue or not event_id:
        return None

    # A bare city is not a defensible venue.
    city = resolve_city(venue)
    if fold(venue) == fold(city):
        return None

    return {
        'title': title,
        'date': event_date,
        'url': f'{page_url}#{event_id}',
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'CZ',
        'description': event_description(modal),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def available_years(soup):
    years = set()
    for link in soup.select('a[href]'):
        match = re.search(r'/program/(20\d{2})(?:/)?$', link.get('href', ''))
        if match:
            years.add(int(match.group(1)))
    return sorted(years)


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    index = get_soup(session, PROGRAM_URL)
    linked_years = available_years(index)
    if not linked_years:
        return []
    # Older year pages remain public even after their navigation buttons are
    # removed.  2022 is the first year for which this calendar has events.
    pending_years = list(range(ARCHIVE_START_YEAR, max(linked_years) + 1))
    seen_years = set()
    records = []

    while pending_years:
        year = pending_years.pop(0)
        if year in seen_years:
            continue
        seen_years.add(year)
        page_url = urljoin(SOURCE_URL, f'program/{year}')
        try:
            soup = get_soup(session, page_url)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape programme year',
                event='crawler_page_failed',
                level='warning',
                url=page_url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        for linked_year in available_years(soup):
            if linked_year not in seen_years and linked_year not in pending_years:
                pending_years.append(linked_year)
        for modal in soup.select('div.modal[id^="event"]'):
            record = parse_event(modal, year, page_url)
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda item: (item['date'], item['time_from'] or '', item['title'], item['url']),
    )


class KsoCzCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='kso_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    KsoCzCrawler().run()


if __name__ == '__main__':
    main()
