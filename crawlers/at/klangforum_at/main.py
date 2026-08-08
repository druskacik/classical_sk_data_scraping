import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.klangforum.at/'
CALENDAR_URL = urljoin(SOURCE_URL, 'calendar')
SOURCE = 'Klangforum Wien'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

# The ensemble tours internationally. Webflow exposes the full venue as one
# string, so known city names (and a few venue-only entries) resolve geography.
LOCATIONS = {
    'wien': ('Wien', 'AT'), 'salzburg': ('Salzburg', 'AT'),
    'graz': ('Graz', 'AT'), 'innsbruck': ('Innsbruck', 'AT'),
    'bregenz': ('Bregenz', 'AT'), 'erl': ('Erl', 'AT'),
    'bludenz': ('Bludenz', 'AT'), 'schwaz': ('Schwaz', 'AT'),
    'berlin': ('Berlin', 'DE'), 'heidelberg': ('Heidelberg', 'DE'),
    'donaueschingen': ('Donaueschingen', 'DE'), 'witten': ('Witten', 'DE'),
    'hannover': ('Hannover', 'DE'), 'hamburg': ('Hamburg', 'DE'),
    'bochum': ('Bochum', 'DE'), 'dortmund': ('Dortmund', 'DE'),
    'düsseldorf': ('Düsseldorf', 'DE'), 'darmstadt': ('Darmstadt', 'DE'),
    'stuttgart': ('Stuttgart', 'DE'), 'essen': ('Essen', 'DE'),
    'köln': ('Köln', 'DE'), 'görlitz': ('Görlitz', 'DE'),
    'bozen': ('Bozen', 'IT'), 'agrigento': ('Agrigento', 'IT'),
    'reggio emilia': ('Reggio Emilia', 'IT'),
    'madrid': ('Madrid', 'ES'), 'san sebastián': ('San Sebastián', 'ES'),
    'badajoz': ('Badajoz', 'ES'), 'santiago de compostela': ('Santiago de Compostela', 'ES'),
    'paris': ('Paris', 'FR'), 'dijon': ('Dijon', 'FR'),
    'amsterdam': ('Amsterdam', 'NL'), 'rotterdam': ('Rotterdam', 'NL'),
    'prag': ('Prag', 'CZ'), 'prague': ('Prag', 'CZ'),
    'budapest': ('Budapest', 'HU'), 'helsinki': ('Helsinki', 'FI'),
    'tokyo': ('Tokyo', 'JP'), 'peking': ('Peking', 'CN'),
    'beijing': ('Peking', 'CN'), 'hangzhou': ('Hangzhou', 'CN'),
    'hong kong': ('Hong Kong', 'HK'), 'tongyeong': ('Tongyeong', 'KR'),
    'katowice': ('Katowice', 'PL'), 'warschau': ('Warschau', 'PL'),
    'zgorzelec': ('Zgorzelec', 'PL'), 'zagreb': ('Zagreb', 'HR'),
    'london': ('London', 'GB'), 'new york': ('New York', 'US'),
    'washington': ('Washington', 'US'), 'cambridge (ma)': ('Cambridge', 'US'),
    'tel aviv': ('Tel Aviv', 'IL'), 'luzern': ('Luzern', 'CH'),
    'larissa': ('Larissa', 'GR'), 'antwerpen': ('Antwerpen', 'BE'),
}

VENUE_LOCATIONS = {
    'concert hall vatroslav lisinski': ('Zagreb', 'HR'),
    'zagreb youth theatre': ('Zagreb', 'HR'),
    'daegu opera house': ('Daegu', 'KR'),
    'conservatoire darius milhaud': ('Aix-en-Provence', 'FR'),
    'kug - mumuth': ('Graz', 'AT'),
    'wiener konzerthaus': ('Wien', 'AT'),
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value):
    try:
        return datetime.strptime(clean_text(value), '%B %d, %Y').date().isoformat()
    except ValueError:
        return None


def resolve_location(value):
    location = clean_text(value).strip(' ,')
    lowered = location.casefold()
    city_country = next(
        (result for marker, result in LOCATIONS.items() if marker.casefold() in lowered),
        None,
    )
    if not city_country:
        city_country = next(
            (result for marker, result in VENUE_LOCATIONS.items() if marker in lowered),
            None,
        )
    if not city_country:
        return None, None, None

    city, country_code = city_country
    venue = re.sub(
        rf',?\s*{re.escape(city)}(?:,?\s*(?:Österreich|Austria|Greece|Israel))?\s*$',
        '',
        location,
        flags=re.I,
    ).strip(' ,')
    # Some listings contain only a city. That is useful geography but is not a
    # defensible venue, so those records are deliberately skipped.
    if not venue or venue.casefold() == city.casefold():
        return None, None, None
    return venue, city, country_code


def listing_pages(session):
    page = 1
    while True:
        response = session.get(
            CALENDAR_URL,
            params={'615f35aa_page': page},
            timeout=45,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        items = soup.select('.calendar-event')
        if not items:
            break
        yield from items
        next_page = soup.select_one(
            f'a[href*="615f35aa_page={page + 1}"]'
        )
        if not next_page:
            break
        page += 1


def listing_record(item):
    link = item.select_one('a.event-item-link[href]')
    title = clean_text(item.select_one('.event-item-title'))
    event_date = parse_date(item.select_one('.event-item-date'))
    raw_time = clean_text(item.select_one('.event-item-time'))
    match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', raw_time)
    venue, city, country_code = resolve_location(item.select_one('.event-item-location'))
    url = urljoin(SOURCE_URL, link.get('href')) if link else ''
    if not title or not event_date or not url or not venue or not city or not country_code:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': f'{int(match.group(1)):02d}:{match.group(2)}' if match else None,
        'venue': venue,
        'city': city,
        'country_code': country_code,
    }


def detail_description(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    parts = []
    for selector in ('.event-section-about .w-richtext', '.event-section-credits .w-richtext'):
        text = clean_text(soup.select_one(selector))
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


class KlangforumAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='klangforum_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        records = [record for item in listing_pages(session) if (record := listing_record(item))]

        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {
                executor.submit(detail_description, session, record['url']): record
                for record in records
            }
            for future in as_completed(futures):
                record = futures[future]
                try:
                    record['description'] = future.result()
                except requests.RequestException as error:
                    record['description'] = None
                    log_message(
                        'Failed to scrape Klangforum event detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=record['url'],
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )

        return sorted(
            records,
            key=lambda item: (item['date'], item['time_from'] or '', item['title'], item['venue']),
        )


def main():
    KlangforumAtCrawler().run()


if __name__ == '__main__':
    main()
