import re
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://festival.cz'
PROGRAM_URL = f'{BASE_URL}/program/'
SOURCE_NAME = 'Pražské jaro'
SOURCE_URL = BASE_URL
DEFAULT_CITY = 'Praha'

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
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def clean_inline(text):
    return clean_text(text).replace('\n', ' ')


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_date_from_node(node):
    if not node:
        return None

    day_el = node.select_one('.event-item__date-day, .event__date-day')
    month_el = node.select_one('.event-item__date-month, .event__date-month')
    year_el = node.select_one('.event-item__date-year, .event__date-year')
    if day_el and month_el and year_el:
        day = int(clean_inline(day_el.get_text()))
        month = int(clean_inline(month_el.get_text()))
        year = int(clean_inline(year_el.get_text()))
        return f'{year:04d}-{month:02d}-{day:02d}'

    text = clean_inline(node.get_text(' ', strip=True))
    match = re.search(r'\b(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(20\d{2})\b', text)
    if match:
        day, month, year = [int(part) for part in match.groups()]
        return f'{year:04d}-{month:02d}-{day:02d}'

    return None


def normalize_time(raw):
    if not raw:
        return None
    match = re.search(r'\b([01]?\d|2[0-3])[:.](\d{2})\b', raw)
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def parse_time_from_listing(item):
    day_el = item.select_one('.event-item__day')
    return normalize_time(day_el.get_text(' ', strip=True) if day_el else '')


def parse_time_from_detail(event):
    date_el = event.select_one('.event__date')
    return normalize_time(date_el.get_text(' ', strip=True) if date_el else '')


def parse_time_to(event):
    for date_el in event.select('.event__date'):
        text = clean_inline(date_el.get_text(' ', strip=True))
        if 'konec' in text.lower():
            return normalize_time(text)
    return None


def infer_city(title, venue, description):
    haystack = clean_inline(' '.join(part for part in [title, venue, description] if part))
    if re.search(r'\bKřečovic', haystack, re.IGNORECASE):
        return 'Křečovice'
    return DEFAULT_CITY


def extract_section_text(event, selector):
    section = event.select_one(selector)
    if not section:
        return ''
    return clean_text(section.get_text('\n', strip=True))


def extract_description(soup, event):
    parts = []

    subtitle = event.select_one('.event__subtitle')
    if subtitle:
        parts.append(clean_text(subtitle.get_text('\n', strip=True)))

    for selector in [
        '.event__repertoire',
        '.event__interprets',
        '.event__description',
        '.event__content-text',
        '.event__text-content',
    ]:
        text = extract_section_text(event, selector)
        if text:
            parts.append(text)

    for text_image in soup.select('.event-blocks .text-image__content'):
        text = clean_text(text_image.get_text('\n', strip=True))
        if text:
            parts.append(text)

    if not parts:
        content = event.select_one('.event__content') or event
        parts.append(clean_text(content.get_text('\n', strip=True)))

    description = clean_text('\n\n'.join(dict.fromkeys(part for part in parts if part)))
    return description or None


def extract_listing_concert(item):
    link = item.select_one('a.event-item__image-link[href], a.event-item__more-link[href]')
    title_el = item.select_one('.event-item__title')
    if not link or not title_el:
        return None

    date_el = item.select_one('.event-item__date')
    venue_el = item.select_one('.event-item__place')
    desc_el = item.select_one('.event-item__desc')

    url = urljoin(BASE_URL, link.get('href'))
    title = clean_inline(title_el.get_text(' ', strip=True))
    venue = clean_inline(venue_el.get_text(' ', strip=True)) if venue_el else None
    description = clean_text(desc_el.get_text('\n', strip=True)) if desc_el else None

    return {
        'title': title,
        'date': parse_date_from_node(date_el),
        'url': url,
        'time_from': parse_time_from_listing(item),
        'time_to': None,
        'venue': venue,
        'city': infer_city(title, venue, description),
        'description': description,
        'type': 'concert',
    }


def extract_detail_concert(session, concert):
    soup = get_soup(session, concert['url'])
    event = soup.select_one('.event')
    if not event:
        return concert

    title_el = event.select_one('.event__title, h1')
    venue_el = event.select_one('.event__place-name')
    date_el = event.select_one('.event__date')
    description = extract_description(soup, event)

    title = clean_inline(title_el.get_text(' ', strip=True)) if title_el else concert['title']
    venue = clean_inline(venue_el.get_text(' ', strip=True)) if venue_el else concert['venue']
    date = parse_date_from_node(date_el) or concert['date']
    time_from = parse_time_from_detail(event) or concert['time_from']

    concert.update({
        'title': title,
        'date': date,
        'time_from': time_from,
        'time_to': parse_time_to(event),
        'venue': venue,
        'city': infer_city(title, venue, description),
        'description': description or concert['description'],
    })
    return concert


def get_listing_concerts(session):
    soup = get_soup(session, PROGRAM_URL)
    concerts = []
    seen_urls = set()

    for item in soup.select('.event-item'):
        concert = extract_listing_concert(item)
        if not concert or not concert['date'] or not concert['url']:
            continue
        if concert['url'] in seen_urls:
            continue
        seen_urls.add(concert['url'])
        concerts.append(concert)

    return concerts


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = []
    for concert in get_listing_concerts(session):
        try:
            concerts.append(extract_detail_concert(session, concert))
        except requests.RequestException as exc:
            print(f'Failed to scrape {concert["url"]}: {exc}')
            concerts.append(concert)

    return [concert for concert in concerts if concert['title'] and concert['date']]


class FestivalCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='festival_cz',
        source=SOURCE_NAME,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'time_to',
            'venue',
            'city',
            'description',
            'type',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    FestivalCrawler().run()


if __name__ == '__main__':
    main()
