import json
import re
from datetime import datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.ensembleinegal.cz'
CONCERTS_URL = f'{BASE_URL}/koncerty-inegal/'
AJAX_URL = f'{BASE_URL}/wp-admin/admin-ajax.php'
SOURCE = 'Ensemble Inégal'
SOURCE_URL = BASE_URL

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
}


def clean_text(value):
    if not value:
        return ''
    text = unescape(str(value)).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_date(value):
    try:
        return datetime.strptime(clean_text(value), '%d. %m. %Y').strftime('%Y-%m-%d')
    except ValueError:
        return None


def parse_time(value):
    value = clean_text(value)
    if not re.fullmatch(r'\d{1,2}:\d{2}', value) or value == '00:00':
        return None
    hour, minute = value.split(':')
    return f'{int(hour):02d}:{minute}'


def extract_nonce(html):
    match = re.search(
        r'concertsManagerData\s*=\s*(\{.*?\})\s*;',
        html,
        flags=re.DOTALL,
    )
    if match:
        try:
            return json.loads(match.group(1)).get('nonce')
        except json.JSONDecodeError:
            pass

    match = re.search(r'["\']nonce["\']\s*:\s*["\']([^"\']+)', html)
    return match.group(1) if match else None


def fetch_details(session, concert_id, nonce):
    if not nonce:
        return ''

    response = session.post(
        AJAX_URL,
        data={
            'action': 'get_concert_details',
            'nonce': nonce,
            'concert_id': concert_id,
        },
        headers={
            'Referer': CONCERTS_URL,
            'X-Requested-With': 'XMLHttpRequest',
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get('success'):
        return ''

    detail_html = payload.get('data', {}).get('html', '')
    detail_soup = BeautifulSoup(detail_html, 'html.parser')
    description = detail_soup.select_one('.concert-description')
    return clean_text(description.get_text('\n', strip=True)) if description else ''


def split_location(value):
    location = clean_text(value)
    parts = [clean_text(part) for part in location.split(',') if clean_text(part)]
    if not parts:
        return None, None

    first = parts[0]
    normalized = re.sub(r'\s*/[A-Z]{2}/\s*$', '', first, flags=re.IGNORECASE)
    city_names = {
        'BŘEZNICE': 'Březnice',
        'DRESDEN': 'Dresden',
        'FRÝDEK - MÍSTEK': 'Frýdek-Místek',
        'KOPŘIVNICE': 'Kopřivnice',
        'PRAHA': 'Praha',
    }

    if normalized.upper() == 'BZÍ' and len(parts) > 1:
        city = 'Železný Brod'
        venue_parts = parts[2:]
    else:
        city = city_names.get(normalized.upper(), normalized.title())
        venue_parts = parts[1:]

    venue = clean_text(', '.join(venue_parts)) or None
    return city or None, venue


def extract_concert(card, session, nonce):
    concert_id = clean_text(card.get('data-id'))
    title_node = card.select_one('.concert-title')
    date_node = card.select_one('.concert-date')
    if not concert_id or not title_node or not date_node:
        return None

    title = clean_text(title_node.get_text(' ', strip=True))
    date = parse_date(date_node.get_text(' ', strip=True))
    if not title or not date:
        return None

    time_node = card.select_one('.concert-time')
    location_node = card.select_one('.concert-venue')
    preview_node = card.select_one('.concert-description-preview')
    location = clean_text(location_node.get_text(' ', strip=True)) if location_node else ''
    preview = clean_text(preview_node.get_text('\n', strip=True)) if preview_node else ''
    city, venue = split_location(location)

    try:
        full_description = fetch_details(session, concert_id, nonce)
    except (requests.RequestException, ValueError):
        full_description = ''

    description_parts = [full_description or preview]
    if location:
        description_parts.insert(0, f'Místo: {location}')

    return {
        'title': title,
        'date': date,
        'url': f'{CONCERTS_URL}#concert-{concert_id}',
        'time_from': parse_time(time_node.get_text(' ', strip=True)) if time_node else None,
        'venue': venue,
        'city': city,
        'country_code': 'CZ',
        'description': clean_text('\n\n'.join(part for part in description_parts if part)) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(CONCERTS_URL, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    nonce = extract_nonce(response.text)
    concerts = []
    for card in soup.select('.concerts-list .concert-card[data-id]'):
        concert = extract_concert(card, session, nonce)
        if concert:
            concerts.append(concert)
    return concerts


class EnsembleInegalCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ensembleinegal_cz',
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
        dedupe_subset=['title', 'date', 'url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    EnsembleInegalCrawler().run()


if __name__ == '__main__':
    main()
