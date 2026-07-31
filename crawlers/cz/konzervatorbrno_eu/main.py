import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://konzervatorbrno.eu'
SOURCE_URL = f'{BASE_URL}/'
EVENTS_API_URL = f'{BASE_URL}/wp-admin/admin-ajax.php'
SOURCE = 'Konzervatoř Brno'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
}


def clean_text(value):
    if not value:
        return None
    value = value.replace('\xa0', ' ').replace('\u202f', ' ')
    value = re.sub(r'[ \t\r\f\v]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip() or None


def html_to_text(value):
    if not value:
        return None
    return clean_text(BeautifulSoup(value.replace('</br>', '<br>'), 'html.parser').get_text('\n'))


def discover_events(session):
    today = date.today()
    response = session.get(
        EVENTS_API_URL,
        params={
            'action': 'eventorganiser-fullcal',
            'start': today.isoformat(),
            # A long range avoids depending on the calendar's visible month.
            'end': (today + timedelta(days=730)).isoformat(),
            'timeformat': 'G:i',
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def extract_city(venue, map_url):
    location = venue or ''
    if map_url:
        location += ' ' + parse_qs(urlparse(map_url).query).get('q', [''])[0]

    match = re.search(
        r'\b(Brno|Praha|Ostrava|Olomouc|Zlín|Jihlava|'
        r'České Budějovice|Hradec Králové|Pardubice|Plzeň)\b',
        location,
        flags=re.IGNORECASE,
    )
    if match:
        cities = {
            'brno': 'Brno',
            'praha': 'Praha',
            'ostrava': 'Ostrava',
            'olomouc': 'Olomouc',
            'zlín': 'Zlín',
            'jihlava': 'Jihlava',
            'české budějovice': 'České Budějovice',
            'hradec králové': 'Hradec Králové',
            'pardubice': 'Pardubice',
            'plzeň': 'Plzeň',
        }
        return cities[match.group(1).lower()]
    return 'Brno'


def extract_detail(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    article = soup.select_one('article.event')
    if not article:
        raise ValueError(f'Event article not found at {url}')

    title_element = article.select_one('h1.entry-title')
    meta_items = article.select('.eo-event-meta > li')
    when = clean_text(meta_items[0].get_text(' ', strip=True)) if meta_items else None
    venue = clean_text(meta_items[1].get_text(' ', strip=True)) if len(meta_items) > 1 else None
    when = re.sub(r'^Kdy:\s*', '', when or '', flags=re.IGNORECASE) or None
    venue = re.sub(r'^Kde:\s*', '', venue or '', flags=re.IGNORECASE) or None

    content = article.select_one('.entry-content')
    description = None
    if content:
        content = BeautifulSoup(str(content), 'html.parser')
        for unwanted in content.select(
            '.eventorganiser-event-meta, .oacs_spl_before_button, '
            '.oacs-spl-like-button-wrapper, .oacs_spl_after_button, img, script, style'
        ):
            unwanted.decompose()
        description = clean_text(content.get_text('\n', strip=True))

    map_element = article.select_one('.eo-event-venue-map iframe[src]')
    map_url = map_element.get('src') if map_element else None
    return {
        'title': clean_text(title_element.get_text(' ', strip=True)) if title_element else None,
        'when': when,
        'venue': venue,
        'city': extract_city(venue, map_url),
        'description': description,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    concerts = []
    seen = set()

    for event in discover_events(session):
        url = event.get('url')
        start = event.get('start')
        if not url or not start or url in seen:
            continue
        seen.add(url)

        try:
            detail = extract_detail(session, url)
        except (requests.RequestException, ValueError) as exc:
            print(f'Failed to scrape detail {url}: {exc}')
            detail = {}

        title = detail.get('title') or clean_text(event.get('title'))
        if not title:
            print(f'Skipping {url}: missing title')
            continue

        concerts.append({
            'title': title,
            'date': start[:10],
            'url': url,
            'time_from': start[11:16] if len(start) >= 16 else None,
            'venue': detail.get('venue'),
            'city': detail.get('city') or 'Brno',
            'country_code': 'CZ',
            'description': detail.get('description') or html_to_text(event.get('description')),
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })

    return concerts


class KonzervatorBrnoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='konzervatorbrno_eu',
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
    KonzervatorBrnoCrawler().run()


if __name__ == '__main__':
    main()
