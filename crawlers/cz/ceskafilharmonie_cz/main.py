import json
import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.ceskafilharmonie.cz'
PROGRAM_URL = f'{BASE_URL}/program/'
SOURCE_NAME = 'Česká filharmonie'
SOURCE_URL = BASE_URL

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def clean_text(text):
    if not text:
        return ''
    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_json_ld(script):
    raw = unescape(script.get_text('', strip=True))
    raw = raw.replace('&nbsp;', ' ')
    return json.loads(raw)


def parse_datetime(value):
    if not value:
        return None
    value = value.split('.')[0]
    return datetime.fromisoformat(value)


KNOWN_CITY_NAMES = [
    'Praha',
    'Litomyšl',
    'Bratislava',
    'Bad Kissingen',
    'Stockholm',
    'Helsinky',
    'Lucern',
]


def infer_city(text):
    text = clean_text(text)
    for city in KNOWN_CITY_NAMES:
        if re.search(rf'\b{re.escape(city)}\b', text, re.IGNORECASE):
            return city
    return None


def split_location(location, title='', url=''):
    location = clean_text(location)
    if not location:
        return None, None

    if ' — ' in location:
        left, right = [clean_text(part) for part in location.split(' — ', 1)]
        if left == 'Rudolfinum':
            return 'Praha', right
        city = infer_city(left) or infer_city(title) or infer_city(url)
        venue = right if city and city != right else left
        return city, venue

    parts = [part.strip() for part in location.split(',') if part.strip()]
    if len(parts) >= 2:
        return clean_text(parts[-1]), clean_text(parts[0])

    return infer_city(title) or infer_city(url), location


def get_event_detail_schema(soup, url):
    for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
        try:
            data = parse_json_ld(script)
        except (json.JSONDecodeError, TypeError):
            continue

        if data.get('@type') != 'Event':
            continue

        data_url = schema_url(data)
        if not data_url or urljoin(BASE_URL, data_url) == url:
            return data

    return {}


def get_schema_location(schema):
    location = schema.get('location') or {}
    if not isinstance(location, dict):
        return None, None

    venue = clean_text(location.get('name'))
    address = location.get('address') or {}
    city = None
    if isinstance(address, dict):
        city = clean_text(address.get('addressLocality')).replace(' 1', '')

    if venue and ',' in venue:
        venue = clean_text(venue.split(',', 1)[1])

    return city or None, venue or None


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def get_page_count(soup):
    pages = [1]
    for link in soup.select('a[href*="stranka="]'):
        match = re.search(r'stranka=(\d+)', link.get('href', ''))
        if match:
            pages.append(int(match.group(1)))
    return max(pages)


def schema_url(schema):
    offers = schema.get('offers')
    if isinstance(offers, list) and offers:
        return offers[0].get('url')
    if isinstance(offers, dict):
        return offers.get('url')
    return None


def get_event_schema(card, url, start):
    script = card.find_next_sibling('script', attrs={'type': 'application/ld+json'})
    if not script:
        return {}
    try:
        data = parse_json_ld(script)
    except (json.JSONDecodeError, TypeError):
        return {}
    if data.get('@type') != 'Event':
        return {}

    data_url = schema_url(data)
    data_start = parse_datetime(data.get('startDate'))
    if data_url and urljoin(BASE_URL, data_url) != url:
        return {}
    if data_start and start and data_start != start:
        return {}

    return data


def extract_card_concert(card):
    detail_link = card.select_one('.event__headline a[href*="/event/"]')
    time_el = card.select_one('.event__dates time[datetime]')
    if not detail_link or not time_el:
        return None

    url = urljoin(BASE_URL, detail_link.get('href'))
    start = parse_datetime(time_el.get('datetime'))
    schema = get_event_schema(card, url, start)
    end = parse_datetime(schema.get('endDate'))

    tags = [clean_text(tag.get_text(' ', strip=True)) for tag in card.select('.event__tags .badge')]
    venue = tags[1] if len(tags) > 1 else None

    body_el = card.select_one('.event__body')
    summary = clean_text(body_el.get_text('\n', strip=True)) if body_el else ''
    description = clean_text(schema.get('description') or summary) or None

    return {
        'title': clean_text(schema.get('name') or detail_link.get_text(' ', strip=True)),
        'date': start.date().isoformat() if start else None,
        'time_from': start.strftime('%H:%M') if start else None,
        'time_to': end.strftime('%H:%M') if end else None,
        'url': url,
        'venue': venue,
        'city': None,
        'type': tags[-1] if tags else None,
        'description': description,
    }


def extract_detail_data(session, url):
    soup = get_soup(session, url)
    main = soup.select_one('main.page--event-detail') or soup.select_one('main')
    schema = get_event_detail_schema(soup, url)
    title = clean_text(schema.get('name'))

    city, venue = get_schema_location(schema)
    location_el = main.select_one('.icon-label') if main else None
    if location_el and (not city or not venue):
        display_city, display_venue = split_location(location_el.get_text(' ', strip=True), title, url)
        city = city or display_city
        venue = venue or display_venue

    description_parts = []
    intro_box = main.select_one('.hero-section > .box .wrapper') if main else None
    if intro_box:
        description_parts.append(clean_text(intro_box.get_text('\n', strip=True)))

    detail_text = main.select_one('section.narrow-content .section__body') if main else None
    if detail_text:
        description_parts.append(clean_text(detail_text.get_text('\n', strip=True)))

    if not description_parts and main:
        description_parts.append(clean_text(main.get_text('\n', strip=True)))

    return {
        'city': city,
        'venue': venue,
        'description': clean_text('\n\n'.join(part for part in description_parts if part)) or None,
    }


def get_listing_concerts(session):
    first_page = get_soup(session, PROGRAM_URL)
    concerts = []
    page_count = get_page_count(first_page)

    for page in range(1, page_count + 1):
        soup = first_page if page == 1 else get_soup(session, f'{PROGRAM_URL}?stranka={page}')
        for card in soup.select('div.event.style-default'):
            concert = extract_card_concert(card)
            if concert and concert['title'] and concert['date']:
                concerts.append(concert)

    return concerts


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = get_listing_concerts(session)
    details_cache = {}

    for concert in concerts:
        url = concert['url']
        if url not in details_cache:
            try:
                details_cache[url] = extract_detail_data(session, url)
            except requests.RequestException as exc:
                print(f'Failed to scrape {url}: {exc}')
                details_cache[url] = {}

        details = details_cache[url]
        concert['city'] = details.get('city') or concert.get('city') or 'Praha'
        concert['venue'] = details.get('venue') or concert.get('venue') or 'Rudolfinum'
        concert['description'] = details.get('description') or concert.get('description')

    return concerts


class CeskaFilharmonieCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ceskafilharmonie_cz',
        source=SOURCE_NAME,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'time_from', 'time_to', 'url', 'venue', 'city', 'type', 'description'],
        dedupe_subset=['title', 'date', 'time_from', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    CeskaFilharmonieCrawler().run()


if __name__ == '__main__':
    main()
