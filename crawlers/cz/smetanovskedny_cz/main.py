import re
from datetime import date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.smetanovskedny.cz/'
PROGRAM_URL = urljoin(BASE_URL, 'program/')
SOURCE = 'Smetanovské dny'
DEFAULT_CITY = 'Plzeň'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'cs,en;q=0.8',
}

NON_CONCERT_TITLES = re.compile(
    r'^(?:výstava\b|\d+\.\s*plzeňské mezioborové sympozium\b|interpretační seminář\b)',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    value = unescape(value).replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def fetch_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def element_text(soup, selector, separator=' '):
    element = soup.select_one(selector)
    return clean_text(element.get_text(separator, strip=True)) if element else ''


def parse_iso_date(value):
    match = re.match(r'(20\d{2})-(\d{2})-(\d{2})', value or '')
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2}):(\d{2})\b', clean_text(value))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def split_place(value):
    value = clean_text(value)
    if not value:
        return None, DEFAULT_CITY

    city, separator, venue = value.partition(',')
    if separator and city.strip() in {'Blovice', 'Dobřany', 'Domažlice', 'Chrást', 'Klatovy'}:
        return venue.strip() or None, city.strip()
    return value, DEFAULT_CITY


def extract_listing(session):
    soup = fetch_soup(session, PROGRAM_URL)
    container = soup.select_one('.program__tab--all')
    if not container:
        return []

    records = []
    for item in container.select('article.event'):
        link = item.select_one('a.event__link[href]')
        time_element = item.select_one('time.event__date')
        title = element_text(item, '.event__heading')
        time_from = parse_time(time_element.get_text(' ', strip=True) if time_element else '')
        if not link or not title or not time_from or NON_CONCERT_TITLES.search(title):
            continue

        venue, city = split_place(element_text(item, '.event__place'))
        records.append({
            'title': title,
            'date': parse_iso_date(time_element.get('datetime', '') if time_element else ''),
            'url': urljoin(PROGRAM_URL, link.get('href')),
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'CZ',
            'description': element_text(item, '.event__subtitle') or None,
            'source_url': BASE_URL,
            'source': SOURCE,
        })
    return records


def extract_detail(session, url):
    soup = fetch_soup(session, url)
    time_element = soup.select_one('.detail-intro__time')
    venue, city = split_place(element_text(soup, '.detail-intro__place'))

    description_parts = []
    subtitle = element_text(soup, '.detail-intro__room', '\n')
    body = element_text(soup, '.detail-content', '\n')
    if body.lower().startswith('podrobné informace'):
        body = body[len('podrobné informace'):].strip()
    for part in (subtitle, body):
        if part and part not in description_parts:
            description_parts.append(part)

    return {
        'title': element_text(soup, '.detail-intro__heading') or None,
        'date': parse_iso_date(time_element.get('datetime', '') if time_element else ''),
        'time_from': parse_time(time_element.get_text(' ', strip=True) if time_element else ''),
        'venue': venue,
        'city': city,
        'description': clean_text('\n\n'.join(description_parts)) or None,
    }


class SmetanovskeDnyCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='smetanovskedny_cz',
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
        dedupe_subset=['title', 'date', 'time_from', 'url'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        records = extract_listing(session)

        for record in records:
            try:
                details = extract_detail(session, record['url'])
            except requests.RequestException as exc:
                print(f'Failed to scrape concert detail {record["url"]}: {exc}')
                continue
            for field, value in details.items():
                if value:
                    record[field] = value

        unique = {}
        for record in records:
            if record.get('title') and record.get('date'):
                key = (record['title'], record['date'], record.get('time_from'), record['url'])
                unique[key] = record
        return list(unique.values())


def main():
    SmetanovskeDnyCrawler().run()


if __name__ == '__main__':
    main()
