import json
import re
from datetime import date, datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.mfo.cz/'
PROGRAM_URL = f'{BASE_URL}program/'
SOURCE = 'Moravská filharmonie Olomouc'
FIRST_ARCHIVE_YEAR = 2022

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

# The bare Olomouc hall names are used throughout MFO's site. More specific
# markers protect touring performances from receiving the institution's home city.
CITY_MARKERS = {
    'aarau': 'Aarau',
    'basel': 'Basel',
    'basilej': 'Basel',
    'brno': 'Brno',
    'bydhošť': 'Bydhošť',
    'grafenegg': 'Grafenegg',
    'hukvaldy': 'Hukvaldy',
    'jihlava': 'Jihlava',
    'jaroměřice nad rokytnou': 'Jaroměřice nad Rokytnou',
    'jeseník': 'Jeseník',
    'katovice': 'Katovice',
    'kirchstetten': 'Kirchstetten',
    'krakov': 'Krakov',
    'krnov': 'Krnov',
    'kroměříž': 'Kroměříž',
    'lublin': 'Lublin',
    'lucern': 'Lucern',
    'litomyšl': 'Litomyšl',
    'opole': 'Opolí',
    'opolí': 'Opolí',
    'olomouc': 'Olomouc',
    'ostrava': 'Ostrava',
    'přerov': 'Přerov',
    'prostějov': 'Prostějov',
    'slavkov u brna': 'Slavkov u Brna',
    'šternberk': 'Šternberk',
    'štětín': 'Štětín',
    'šumperk': 'Šumperk',
    'thun': 'Thun',
    'uherské hradiště': 'Uherské Hradiště',
    'uničov': 'Uničov',
    'valtice': 'Valtice',
    'varaždín': 'Varaždín',
    'varšava': 'Varšava',
    'vídeň': 'Vídeň',
    'zábřeh': 'Zábřeh',
    'český krumlov': 'Český Krumlov',
}

EXACT_VENUE_CITIES = {
    'Červený kostel v Olomouci': 'Olomouc',
    'Ferenc Liszt Academy': 'Budapešť',
    'Koncertní sál Lisinski': 'Záhřeb',
    'Kodály Centre': 'Pécs',
    'Mozartův sál': 'Olomouc',
    'Reduta': 'Olomouc',
    'Rektorský palác': 'Dubrovník',
    'chrám sv. Mořice': 'Olomouc',
    'kostel Panny Marie Sněžné': 'Olomouc',
    'kostel sv. Mořice': 'Olomouc',
}


def clean_text(value):
    if not value:
        return ''
    text = unescape(str(value)).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def html_to_text(value):
    if not value:
        return None
    soup = BeautifulSoup(unescape(value), 'html.parser')
    for node in soup.select('script, style'):
        node.decompose()
    return clean_text(soup.get_text('\n', strip=True)) or None


def infer_city(venue):
    if venue in EXACT_VENUE_CITIES:
        return EXACT_VENUE_CITIES[venue]
    lowered = venue.casefold()
    for marker, city in CITY_MARKERS.items():
        if marker in lowered:
            return city
    # These are MFO's home venues and historic Olomouc church/palace listings.
    if any(word in lowered for word in ('reduta', 'mozartův sál')):
        return 'Olomouc'
    return None


def parse_event(data):
    if isinstance(data, list):
        data = next((item for item in data if item.get('@type') == 'Event'), None)
    if not isinstance(data, dict) or data.get('@type') != 'Event':
        return None

    title = clean_text(data.get('name'))
    url = clean_text(data.get('url'))
    venue_data = data.get('location') or {}
    venue = clean_text(venue_data.get('name') if isinstance(venue_data, dict) else '')
    city = infer_city(venue) if venue else None
    try:
        starts_at = datetime.fromisoformat(clean_text(data.get('startDate')))
        event_date = starts_at.date().isoformat()
        if not FIRST_ARCHIVE_YEAR <= starts_at.year <= date.today().year + 2:
            return None
        time_from = starts_at.strftime('%H:%M')
    except (TypeError, ValueError):
        return None

    if not all((title, url, venue, city)):
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'CZ',
        'description': html_to_text(data.get('description')),
        'source_url': BASE_URL,
        'source': SOURCE,
    }


def extract_events(html):
    soup = BeautifulSoup(html, 'html.parser')
    events = []
    for node in soup.select('article.article script[type="application/ld+json"]'):
        try:
            event = parse_event(json.loads(node.string or node.get_text(), strict=False))
        except (json.JSONDecodeError, TypeError):
            continue
        if event:
            events.append(event)
    return events


def fetch_events(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return extract_events(response.text)


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    pages = [PROGRAM_URL]
    pages.extend(
        f'{PROGRAM_URL}?archiv={year}'
        for year in range(FIRST_ARCHIVE_YEAR, date.today().year + 1)
    )

    concerts = {}
    for page in pages:
        for concert in fetch_events(session, page):
            key = (
                concert['url'],
                concert['date'],
                concert['time_from'],
                concert['venue'],
            )
            concerts[key] = concert

    return sorted(
        concerts.values(),
        key=lambda item: (item['date'], item['time_from'] or '', item['title']),
    )


class MfoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mfo_cz',
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
        dedupe_subset=['url', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    MfoCrawler().run()


if __name__ == '__main__':
    main()
