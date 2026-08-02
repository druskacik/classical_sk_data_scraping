import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


BASE_URL = 'https://filharmonie-brno.cz'
SOURCE_URL = f'{BASE_URL}/'
PROGRAM_URL = f'{BASE_URL}/kompletni-program/'
AJAX_URL = f'{BASE_URL}/wp-admin/admin-ajax.php'
SOURCE = 'Filharmonie Brno'

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
    value = value.replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[ \t\r\f\v]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def element_text(parent, selector, separator=' '):
    element = parent.select_one(selector)
    if not element:
        return None
    return clean_text(element.get_text(separator, strip=True)) or None


def parse_date(value):
    match = re.search(r'(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})', value or '')
    if not match:
        return None
    day, month, year = map(int, match.groups())
    return f'{year:04d}-{month:02d}-{day:02d}'


def parse_time(value):
    match = re.search(r'\b(\d{1,2}:\d{2})\b', value or '')
    return match.group(1) if match else None


def infer_city(venue, detail_text):
    combined = clean_text(f'{venue or ""}\n{detail_text or ""}')
    city_match = re.search(
        r'\b(?:\d{3}\s?\d{2}\s+)?'
        r'(Brno(?:-[A-Za-zÁ-ž]+)?|Český Krumlov|Praha|Sterzing|Vipiteno|'
        r'Sankt Pölten|St\. Pölten)\b',
        combined,
        flags=re.IGNORECASE,
    )
    if city_match:
        city = city_match.group(1)
        aliases = {
            'sterzing': 'Sterzing',
            'vipiteno': 'Sterzing',
            'sankt pölten': 'St. Pölten',
            'st. pölten': 'St. Pölten',
            'praha': 'Praha',
            'český krumlov': 'Český Krumlov',
        }
        return aliases.get(city.lower(), 'Brno' if city.lower().startswith('brno') else city)

    local_venues = ('besední dům', 'janáčkovo divadlo', 'hrad špilberk')
    if venue and any(name in venue.lower() for name in local_venues):
        return 'Brno'
    return None


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def discover_event_cards(session):
    page = 1
    seen_urls = set()

    while True:
        response = session.post(
            AJAX_URL,
            data={
                'action': 'filter_programs',
                'koncert': '',
                'zanr': '',
                'misto': '',
                'mesic': '',
                'datum_od': date.today().isoformat(),
                'datum_do': '',
                'program_page': page,
            },
            headers={'Referer': PROGRAM_URL},
            timeout=30,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        cards = soup.select('.program-item')
        if not cards:
            break

        new_cards = 0
        for card in cards:
            link = card.select_one('.program-title a[href], a.program-thumb-link[href]')
            if not link:
                continue
            url = urljoin(BASE_URL, link.get('href'))
            if url in seen_urls or '/program/' not in url:
                continue
            seen_urls.add(url)
            new_cards += 1
            yield card, url

        if new_cards == 0:
            break
        page += 1


def extract_detail(session, url):
    soup = get_soup(session, url)
    title = element_text(soup, 'h1.program-title')
    date_time = element_text(soup, '.program-datum-single')
    venue = element_text(soup, '.program-lokace-text')
    description = element_text(soup, '.program-content', separator='\n')

    location = soup.select_one('.lokace-nazov')
    address = None
    if location and location.parent:
        address = clean_text(location.parent.get_text('\n', strip=True))

    return {
        'title': title,
        'date': parse_date(date_time),
        'time_from': parse_time(date_time),
        'venue': venue,
        'description': description,
        'location_text': address,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    concerts = []

    for card, url in discover_event_cards(session):
        card_date = element_text(card, '.program-date-datum')
        card_time = element_text(card, '.program-datum')
        card_venue = element_text(card, '.program-lokace, .program-misto')
        card_title = element_text(card, '.program-title')
        card_description = element_text(card, '.anotace', separator='\n')

        try:
            detail = extract_detail(session, url)
        except requests.RequestException as exc:
            log_message('Failed to scrape detail', event='crawler_item_failed', level=30, url=url, error_type=type(exc).__name__, error_message=str(exc))
            detail = {}

        title = detail.get('title') or card_title
        concert_date = detail.get('date') or parse_date(card_date)
        if not title or not concert_date:
            log_message('Skipping event with missing title or date', event='crawler_item_skipped', level=30, url=url)
            continue

        venue = detail.get('venue') or card_venue
        description = detail.get('description') or card_description
        concerts.append({
            'title': title,
            'date': concert_date,
            'url': url,
            'time_from': detail.get('time_from') or parse_time(card_time),
            'venue': venue,
            'city': infer_city(venue, detail.get('location_text')),
            'country_code': 'CZ',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })

    return concerts


class FilharmonieBrnoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='filharmonie_brno_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
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
        dedupe_subset=['title', 'date', 'time_from', 'url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    FilharmonieBrnoCrawler().run()


if __name__ == '__main__':
    main()
