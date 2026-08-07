import html
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.insulaorchestra.fr/'
SOURCE = 'Insula orchestra'
EVENTS_API = f'{SOURCE_URL}wp-json/wp/v2/event'
PLACES_API = f'{SOURCE_URL}wp-json/wp/v2/places'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.6',
}

# Some venue branches use a building, rather than a locality, as their parent.
PLACE_DEFAULTS = {
    'la seine musicale': ('Boulogne-Billancourt', 'FR'),
    'auditorium patrick devedjian': ('Boulogne-Billancourt', 'FR'),
    'petite seine': ('Boulogne-Billancourt', 'FR'),
    'philharmonie de paris': ('Paris', 'FR'),
    'grande salle pierre boulez': ('Paris', 'FR'),
    'cite de la musique': ('Paris', 'FR'),
    'hong kong cultural centre': ('Hong Kong', 'HK'),
    'concert hall': ('Hong Kong', 'HK'),
}

FOREIGN_CITIES = {
    'anvers': 'BE',
    'gand': 'BE',
    'linz': 'AT',
    'hambourg': 'DE',
    'dresde': 'DE',
    'breme': 'DE',
    'dortmund': 'DE',
    'budapest': 'HU',
    'monaco': 'MC',
    'hong kong': 'HK',
}


def clean_text(value):
    if not value:
        return ''
    raw = str(value)
    text = (
        BeautifulSoup(raw, 'html.parser').get_text('\n', strip=True)
        if '<' in raw
        else html.unescape(raw)
    )
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def normalized(value):
    value = clean_text(value).lstrip('—- ').lower()
    return ''.join(
        character
        for character in unicodedata.normalize('NFKD', value)
        if not unicodedata.combining(character)
    )


def get_json(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def paginated_collection(session, url):
    records = []
    page = 1
    while True:
        batch = get_json(
            session,
            url,
            params={'per_page': 100, 'page': page, 'orderby': 'id', 'order': 'asc'},
        )
        records.extend(batch)
        if len(batch) < 100:
            return records
        page += 1


def place_lookup(terms):
    by_id = {term['id']: term for term in terms}
    lookup = {}
    for term in terms:
        names = []
        current = term
        while current:
            name = clean_text(current.get('name'))
            if name:
                names.append(name.lstrip('—- '))
            current = by_id.get(current.get('parent'))
        for name in names:
            lookup.setdefault(normalized(name), names)
    return lookup


def resolve_geography(venue, contained_place, lookup):
    candidates = [venue, contained_place]
    hierarchy = []
    for candidate in candidates:
        key = normalized(candidate)
        if key in PLACE_DEFAULTS:
            return PLACE_DEFAULTS[key]
        hierarchy.extend(lookup.get(key, []))

    all_names = candidates + hierarchy
    for name in all_names:
        key = normalized(name)
        city = re.sub(r'\s*\((?:belgique|belgium|allemagne|germany|autriche)\)\s*$', '', key)
        if city in FOREIGN_CITIES:
            return clean_text(re.sub(r'\s*\([^)]*\)\s*$', '', name)), FOREIGN_CITIES[city]

    # In the site's hierarchy a parent with no special building rule is the city.
    if len(hierarchy) > 1:
        city = hierarchy[-1]
        key = normalized(city)
        if key not in PLACE_DEFAULTS:
            return clean_text(re.sub(r'\s*\([^)]*\)\s*$', '', city)), FOREIGN_CITIES.get(key, 'FR')
    return None, None


def parse_start(value):
    if not value:
        return None, None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None, None
    return parsed.date().isoformat(), parsed.strftime('%H:%M')


def description_from_page(soup):
    parts = []
    for heading in soup.find_all(['h5', 'h6']):
        if normalized(heading.get_text()) not in {'programme', 'distribution', 'a propos'}:
            continue
        section = heading.parent
        text = clean_text(section.get_text('\n', strip=True)) if section else ''
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def detail_records(event, places):
    url = clean_text(event.get('link'))
    if not url or '/en/' in url:
        return []
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    description = description_from_page(soup)
    title = clean_text((event.get('title') or {}).get('rendered'))
    records = []

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        nodes = payload.get('@graph', []) if isinstance(payload, dict) else []
        for node in nodes:
            if not isinstance(node, dict) or node.get('@type') != 'Event':
                continue
            event_date, time_from = parse_start(node.get('startDate'))
            location = node.get('location') if isinstance(node.get('location'), dict) else {}
            venue = clean_text(location.get('name'))
            contained = location.get('containedInPlace')
            contained_name = clean_text(contained.get('name')) if isinstance(contained, dict) else ''
            city, country_code = resolve_geography(venue, contained_name, places)
            record_title = title or clean_text(node.get('name'))
            if not all((record_title, event_date, url, venue, city, country_code)):
                continue
            records.append({
                'title': record_title,
                'date': event_date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': country_code,
                'description': description or clean_text(node.get('description')) or None,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    events = paginated_collection(session, EVENTS_API)
    places = place_lookup(paginated_collection(session, PLACES_API))
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(detail_records, event, places): event for event in events}
        for future in as_completed(futures):
            event = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=event.get('link'),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class InsulaOrchestraFrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='insulaorchestra_fr',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        return get_concerts()


def main():
    InsulaOrchestraFrCrawler().run()


if __name__ == '__main__':
    main()
