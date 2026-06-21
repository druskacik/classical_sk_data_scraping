import json
import re
from datetime import date as date_cls
from datetime import datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://collegium1704.com'
SOURCE = 'Collegium 1704'
SOURCE_URL = BASE_URL
PROGRAM_URLS = [
    f'{BASE_URL}/sezona-praha/',
    f'{BASE_URL}/sezona-cv-1704/',
    f'{BASE_URL}/ostatni-koncerty/',
    f'{BASE_URL}/opera/',
    f'{BASE_URL}/vaclav-luks-hostovani/',
]

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

    text = unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_html(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def extract_json_arrays(html):
    decoder = json.JSONDecoder()
    events = []

    for match in re.finditer(r"stecJsonEvents\[[^\]]+\]\s*=\s*", html):
        start = match.end()
        while start < len(html) and html[start].isspace():
            start += 1
        try:
            data, _ = decoder.raw_decode(html[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            events.extend(item for item in data if isinstance(item, dict))

    return events


def parse_datetime(value):
    if not value:
        return None, None

    try:
        parsed = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None, None

    return parsed.date().isoformat(), parsed.strftime('%H:%M')


def normalize_city(value):
    value = clean_text(value)
    if not value:
        return None

    value = re.sub(r'^\d{3}\s*\d{2}\s+', '', value)
    value = re.sub(r'^\d{5}\s+', '', value)
    value = re.sub(r'\s+\d{3}\s*\d{2}$', '', value)
    value = re.sub(r'\s+\d{5}$', '', value)
    value = value.replace('Prague', 'Praha')
    return value or None


def extract_venue(location):
    if not isinstance(location, dict):
        return None

    return clean_text(location.get('full_address') or location.get('address') or location.get('title')) or None


def is_czech_event(event):
    location = event.get('location') if isinstance(event.get('location'), dict) else {}
    country = clean_text(location.get('country', '')).lower()
    city = clean_text(location.get('city', '')).lower()
    address = clean_text(location.get('full_address', '')).lower()
    text = ' '.join([country, city, address])
    return any(token in text for token in ('česká republika', 'ceska republika', 'praha', 'czech'))


def html_to_description(html, title, date, time_from, venue):
    soup = BeautifulSoup(html or '', 'html.parser')
    for tag in soup.select('script, style, form, button, input, iframe'):
        tag.decompose()

    body = clean_text(soup.get_text('\n', strip=True))
    parts = [title]
    if date:
        parts.append(f'{date} {time_from}' if time_from else date)
    if venue:
        parts.append(venue)
    if body:
        parts.append(body)

    return clean_text('\n\n'.join(part for part in parts if part)) or None


def event_categories(event):
    categories = event.get('category')
    if not isinstance(categories, list):
        return []
    return [clean_text(category.get('title')) for category in categories if isinstance(category, dict)]


def parse_event(event):
    title = clean_text(event.get('title'))
    date, time_from = parse_datetime(event.get('start_date'))
    if event.get('all_day') or time_from == '00:00':
        time_from = None
    url = clean_text(event.get('permalink')) or None
    location = event.get('location') if isinstance(event.get('location'), dict) else {}
    venue = extract_venue(location)
    city = normalize_city(location.get('city'))

    if not title or not date or not url:
        return None
    if datetime.strptime(date, '%Y-%m-%d').date() < date_cls.today():
        return None

    return {
        'title': title,
        'date': date,
        'time_from': time_from,
        'time_to': None,
        'url': url,
        'venue': venue,
        'city': city,
        'type': ', '.join(event_categories(event)) or 'concert',
        'description': html_to_description(event.get('description'), title, date, time_from, venue),
    }


def get_concerts():
    session = requests.Session()
    seen = set()
    concerts = []

    for page_url in PROGRAM_URLS:
        try:
            html = get_html(session, page_url)
        except requests.RequestException as exc:
            print(f'Failed to scrape {page_url}: {exc}')
            continue

        for event in extract_json_arrays(html):
            if not is_czech_event(event):
                continue

            concert = parse_event(event)
            if not concert:
                continue

            key = (event.get('id'), concert['date'], concert['time_from'], concert['url'])
            if key in seen:
                continue
            seen.add(key)
            concerts.append(concert)

    return sorted(concerts, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class Collegium1704Crawler(BaseCrawler):
    config = CrawlerConfig(
        slug='collegium1704_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'time_from',
            'time_to',
            'url',
            'venue',
            'city',
            'type',
            'description',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    Collegium1704Crawler().run()


if __name__ == '__main__':
    main()
