import re
from datetime import date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig

BASE_URL = 'https://www.fhk.cz'
SOURCE_URL = f'{BASE_URL}/'
SOURCE = 'Filharmonie Hradec Králové'
CALENDAR_URL = f'{BASE_URL}/123/Kalendar_koncertu/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36'}


def clean(value):
    value = unescape(value or '').replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def event_links(session):
    page = soup(session, CALENDAR_URL)
    return list(dict.fromkeys(
        urljoin(BASE_URL, a['href'].split('?', 1)[0])
        for a in page.select('a[href*="/calendar/"]')
        if re.match(r'^/calendar/\d+/', a['href'])
    ))


def parse_event(session, url):
    page = soup(session, url)
    text = clean(page.get_text('\n', strip=True))
    date_match = re.search(r'\b(\d{1,2})/(\d{1,2})\b', text)
    time_match = re.search(r'\b(\d{1,2}):(\d{2})\b', text)
    if not date_match:
        return None
    day, month = map(int, date_match.groups())
    today = date.today()
    year = today.year if month >= today.month else today.year + 1
    event_date = date(year, month, day)
    if event_date < today:
        return None

    title_el = page.select_one('h1') or page.select_one('.calendar_detail_title')
    title = clean(title_el.get_text(' ', strip=True)) if title_el else clean(page.title.get_text(' ', strip=True)).split(' | ')[0]
    venue = None
    city = 'Hradec Králové'
    # The location is rendered as plain text, immediately after the title.
    for line in clean(page.get_text('\n', strip=True)).splitlines():
        line = clean(line)
        if (',' in line and len(line) < 180 and not line.lower().startswith(('filharmonie', '©'))
                and '(' not in line and not any(word in line.lower() for word in (
                    'po kliknutí', 'podrobnosti', 'cookie', 'zákon', 'souhlas',
                    'ukládat', 'zpracování osobních'))):
            city, venue = (part.strip() for part in line.split(',', 1))
            break
    description = text
    if title and title not in description:
        description = f'{title}\n\n{description}'
    return {'title': title or SOURCE, 'date': event_date.isoformat(), 'url': url,
            'time_from': time_match.group(0) if time_match else None, 'time_to': None,
            'venue': venue, 'city': city, 'description': description,
            'type': 'concert'}


class FhkCrawler(BaseCrawler):
    config = CrawlerConfig(slug='fhk_cz', source=SOURCE, source_url=SOURCE_URL,
        country_code='CZ', columns=['title', 'date', 'url', 'time_from', 'time_to',
        'venue', 'city', 'description', 'type'], dedupe_subset=['title', 'date', 'url'],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)])

    def scrape(self):
        session = requests.Session()
        return [event for url in event_links(session)
                if (event := parse_event(session, url)) is not None]


def main():
    FhkCrawler().run()


if __name__ == '__main__':
    main()
