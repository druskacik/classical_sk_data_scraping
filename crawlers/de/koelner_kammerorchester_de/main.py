import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.koelner-kammerorchester.de/'
SOURCE = 'Kölner Kammerorchester'
LISTING_URLS = (
    urljoin(SOURCE_URL, 'konzerte-2025-2026/'),
    urljoin(SOURCE_URL, 'konzerte-2026-2027/'),
    urljoin(SOURCE_URL, 'archiv/'),
)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}

DATE_RE = re.compile(r'\b(\d{2}\.\d{2}\.\d{4})\b')
TIME_RE = re.compile(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*UHR\b', re.IGNORECASE)

# Venue names on the cards are quite consistent, while touring cities are not
# represented as structured data. Keeping the inference tied to a named venue
# prevents the orchestra's Cologne home from leaking into tour records.
LOCATIONS = (
    ('KÖLNER PHILHARMONIE', 'Kölner Philharmonie', 'Köln', 'DE'),
    ('ROBERT-SCHUMANN-SAAL', 'Robert-Schumann-Saal', 'Düsseldorf', 'DE'),
    ('KUNSTPALAST DÜSSELDORF', 'Robert-Schumann-Saal', 'Düsseldorf', 'DE'),
    ('MARVÃO BURG', 'Burg Marvão', 'Marvão', 'PT'),
    ('MARVAO PORTUGAL', 'Burg Marvão', 'Marvão', 'PT'),
    ('FUNDAÇÃO AMMAIA', 'Fundação Cidade de Ammaia', 'Marvão', 'PT'),
    ('IGREJA DE N.STRA. DA ESTRELA', 'Igreja de Nossa Senhora da Estrela', 'Marvão', 'PT'),
    ('KLOSTER EBERBACH, KREUZGANG', 'Kloster Eberbach, Kreuzgang', 'Eltville am Rhein', 'DE'),
    ('KLOSTER EBERBACH, ELTVILLE AM RHEIN', 'Kloster Eberbach', 'Eltville am Rhein', 'DE'),
    ('BIBLIOTHEKSSAAL OCHSENHAUSEN', 'Bibliothekssaal Ochsenhausen', 'Ochsenhausen', 'DE'),
    ('BONN BEUEL/ST. JOSEF', 'St. Josef', 'Bonn', 'DE'),
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value):
    try:
        return date(int(value[6:10]), int(value[3:5]), int(value[:2])).isoformat()
    except ValueError:
        return None


def resolve_location(text):
    upper = text.upper()
    for marker, venue, city, country_code in LOCATIONS:
        if marker in upper:
            return venue, city, country_code
    return None, None, None


def card_records(card):
    url = urljoin(SOURCE_URL, card.get('data-link-column-url', '').strip())
    title_node = card.find('h1')
    header_node = card.find('h3')
    title = clean_text(title_node)
    header = clean_text(header_node)
    description = clean_text(card)

    if not title or not header or not url or url == SOURCE_URL:
        return []
    if re.search(r'\b(?:ABGESAGT|ABGESAGT|CANCELLED)\b', description, re.IGNORECASE):
        return []

    # Some cards put the hall directly below the date in a separate heading.
    venue, city, country_code = resolve_location(description)
    if not venue or not city or not country_code:
        return []

    dates = []
    for raw_date in DATE_RE.findall(header):
        parsed = parse_date(raw_date)
        if parsed and parsed not in dates:
            dates.append(parsed)
    times = TIME_RE.findall(header)
    if not dates:
        return []

    records = []
    for index, event_date in enumerate(dates):
        raw_time = times[index] if index < len(times) else (times[0] if times else None)
        time_from = f'{int(raw_time[0]):02d}:{raw_time[1] or "00"}' if raw_time else None
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': description or None,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    for listing_url in LISTING_URLS:
        try:
            response = session.get(listing_url, timeout=45)
            response.raise_for_status()
        except requests.RequestException as error:
            log_message(
                'Failed to scrape concert listing',
                event='crawler_page_failed',
                level='warning',
                url=listing_url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue

        soup = BeautifulSoup(response.text, 'html.parser')
        for card in soup.select('div[data-link-column-url]'):
            records.extend(card_records(card))

    unique = {
        (record['url'], record['date'], record['time_from'], record['venue']): record
        for record in records
    }
    return sorted(
        unique.values(),
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class KoelnerKammerorchesterDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='koelner_kammerorchester_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
        upload_target='classical',
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
    KoelnerKammerorchesterDeCrawler().run()


if __name__ == '__main__':
    main()
