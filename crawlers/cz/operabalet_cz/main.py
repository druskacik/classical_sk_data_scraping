import re
from datetime import date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.operabalet.cz'
SOURCE_URL = f'{BASE_URL}/'
PROGRAM_URL = f'{BASE_URL}/program/'
SOURCE = 'Divadlo města Ústí nad Labem'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; classical-bot/1.0)'}


def clean_text(value):
    text = unescape(value or '').replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date_time(text):
    match = re.search(r'\b\d{1,2}\.\s*(\d{1,2})\.\s*(\d{4})\s+(\d{1,2}):(\d{2})\b', text)
    if not match:
        return None, None
    month, year, hour, minute = map(int, match.groups())
    day_match = re.search(r'\b(\d{1,2})\.\s*' + str(month) + r'\.', text)
    if not day_match:
        return None, None
    return f'{year:04d}-{month:02d}-{int(day_match.group(1)):02d}', f'{hour:02d}:{minute:02d}'


def listing_events(soup):
    events = []
    for item in soup.select('.itemlist'):
        title_node = item.select_one('h3')
        date_node = item.select_one('p.date')
        detail = item.select_one('a[href*="/program/"]')
        if not title_node or not date_node or not detail:
            continue
        event_date, time_from = parse_date_time(clean_text(date_node.get_text(' ', strip=True)))
        if not event_date or event_date < date.today().isoformat():
            continue
        venue_node = item.select_one('.location')
        events.append({
            'title': clean_text(title_node.get_text(' ', strip=True)),
            'date': event_date,
            'url': urljoin(BASE_URL, detail['href']),
            'time_from': time_from,
            'venue': clean_text(venue_node.get_text(' ', strip=True)) if venue_node else None,
            'city': 'Ústí nad Labem',
            'country_code': 'CZ',
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return events


def detail_event(session, event):
    response = session.get(event['url'], headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    venue_node = soup.select_one('.detail-venue strong')
    genre_node = soup.select_one('.detail-genre strong')
    date_node = soup.select_one('.detaildate')
    description_node = soup.select_one('.program-description')
    if date_node:
        detail_date, detail_time = parse_date_time(clean_text(date_node.get_text(' ', strip=True)))
        if detail_date:
            event['date'] = detail_date
        if detail_time:
            event['time_from'] = detail_time
    if venue_node:
        event['venue'] = clean_text(venue_node.get_text(' ', strip=True))
    event['description'] = clean_text(description_node.get_text('\n', strip=True)) if description_node else None
    genre = clean_text(genre_node.get_text(' ', strip=True)).lower() if genre_node else ''
    searchable = f"{event['title']} {event['description'] or ''}".lower()
    event['_is_concert'] = 'koncert' in genre or 'koncert' in searchable or searchable.startswith('program:')
    return event


class OperaBaletCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='operabalet_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'url', 'time_from', 'venue', 'city', 'country_code', 'description', 'source_url', 'source'],
        dedupe_subset=['title', 'date', 'url', 'time_from'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        response = session.get(PROGRAM_URL, timeout=30)
        response.raise_for_status()
        events = [detail_event(session, event) for event in listing_events(BeautifulSoup(response.text, 'html.parser'))]
        return sorted((event for event in events if event.pop('_is_concert', False)),
                      key=lambda event: (event['date'], event['time_from'] or '', event['title']))


def main():
    OperaBaletCrawler().run()


if __name__ == '__main__':
    main()
