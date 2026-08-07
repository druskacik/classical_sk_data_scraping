import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.bavaria-klassik.de/'
SOURCE = 'Bavaria Klassik'
ARCHIVE_URL = urljoin(SOURCE_URL, 'konzertreihen/archiv')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}

CURRENT_PARAMS = {
    'tx_intertainevents_events[action]': 'list',
    'type': '100',
}
ARCHIVE_PARAMS = {
    'tx_intertainevents_historie[action]': 'listHistorie',
    'type': '110',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def request_page(url, params, position):
    data = {}
    if params is CURRENT_PARAMS:
        data = {
            'tx_intertainevents_events[position]': str(position),
            'tx_intertainevents_events[monat]': '',
            'tx_intertainevents_events[veranstaltungsort]': '',
            'tx_intertainevents_events[veranstaltungsart]': '',
        }
    else:
        data = {'tx_intertainevents_historie[position]': str(position)}

    last_error = None
    for _attempt in range(3):
        try:
            response = requests.post(
                url,
                params=params,
                data=data,
                headers=HEADERS,
                timeout=45,
            )
            response.raise_for_status()
            return BeautifulSoup(response.text, 'html.parser')
        except requests.RequestException as error:
            last_error = error
    raise last_error


def canonical_event_url(href):
    if not href:
        return ''
    match = re.search(r'(/konzert/\d+/[^?#]+)', href)
    if match:
        return urljoin(SOURCE_URL, match.group(1))
    if 'tx_intertainevents_historie' in href and '/konzert' in href:
        return urljoin(SOURCE_URL, href)
    return ''


def parse_date(value):
    value = clean_text(value).replace('Uhr', '').strip()
    match = re.search(r'(\d{2}\.\d{2}\.\d{2,4})', value)
    if not match:
        return None
    raw = match.group(1)
    for pattern in ('%d.%m.%y', '%d.%m.%Y'):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def parse_time(value):
    match = re.search(r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)', clean_text(value))
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def known_city(venue):
    normalized = venue.casefold()
    mappings = (
        (('starnberg',), 'Starnberg'),
        (('schleißheim', 'schleissheim'), 'Oberschleißheim'),
        (('dachau',), 'Dachau'),
        (('garching',), 'Garching bei München'),
        (('fürstenfeld', 'fuerstenfeld'), 'Fürstenfeldbruck'),
        (
            (
                'münchen', 'munich', 'residenz', 'nymphenburg', 'cuvilli',
                'herkulessaal', 'hofkapelle', 'hofkirche', 'brunnenhof',
                'fürstenried', 'fuerstenried', 'nationalmuseum',
                'max-joseph-saal', 'mars-venussaal', 'kanonenhof',
            ),
            'München',
        ),
    )
    for needles, city in mappings:
        if any(needle in normalized for needle in needles):
            return city
    return None


def detail_location(url):
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or '')
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload.get('@graph', []) if isinstance(payload, dict) else []
        if isinstance(payload, dict):
            items = [payload, *items]
        for item in items:
            if not isinstance(item, dict) or item.get('@type') != 'MusicEvent':
                continue
            location = item.get('location') or {}
            address = location.get('address') or {}
            venue = clean_text(location.get('name'))
            city = clean_text(address.get('addressLocality'))
            country = clean_text(address.get('addressCountry')).upper()
            if venue and city and (not country or country == 'DE'):
                return venue, city
    return None, None


def parse_card(card, location_cache):
    link = card.select_one('a[href*="/konzert/"]') or card.select_one(
        'a[href*="tx_intertainevents_historie"]'
    )
    url = canonical_event_url(link.get('href') if link else '')
    title = clean_text(card.select_one('.bklev__title, .concert_title h3'))
    venue = clean_text(card.select_one('.bklev__venue, .concert_room h5')) or clean_text(
        card.get('data-veranstaltungsort')
    )
    date_node = card.select_one('.bklev__day, .concert_date')
    time_node = card.select_one('.bklev__time, .concert_date')
    event_date = parse_date(date_node)
    time_from = parse_time(time_node)
    description = clean_text(
        card.select_one('.bklev__program, .concert_description')
    ) or None

    city = known_city(venue)
    if not city and venue:
        if venue not in location_cache:
            try:
                location_cache[venue] = detail_location(url)
            except requests.RequestException as error:
                log_message(
                    'Failed to resolve concert location',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                location_cache[venue] = (None, None)
        detail_venue, city = location_cache[venue]
        venue = detail_venue or venue

    if not title or not event_date or not url or not venue or not city:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'DE',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def listing_pages(url, params):
    first = request_page(url, params, 1)
    more = first.select_one('.morebutton')
    total = int(more.get('data-eventcounter', 0)) if more else len(first.select('.concert_single'))
    pages = [first]
    positions = range(11, total + 1, 10)
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(request_page, url, params, position): position
            for position in positions
        }
        for future in as_completed(futures):
            pages.append(future.result())
    return pages


def get_concerts():
    pages = listing_pages(SOURCE_URL, CURRENT_PARAMS)
    pages.extend(listing_pages(ARCHIVE_URL, ARCHIVE_PARAMS))
    location_cache = {}
    records = []
    for page in pages:
        for card in page.select('.concert_single'):
            record = parse_card(card, location_cache)
            if record:
                records.append(record)
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class BavariaKlassikDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bavaria_klassik_de',
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
        dedupe_subset=['url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    BavariaKlassikDeCrawler().run()


if __name__ == '__main__':
    main()
