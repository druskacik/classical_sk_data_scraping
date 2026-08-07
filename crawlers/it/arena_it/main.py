import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.arena.it/'
CALENDAR_URL = urljoin(SOURCE_URL, 'calendario/')
SOURCE = 'Fondazione Arena di Verona'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'it-IT,it;q=0.9,en;q=0.7',
}

# The calendar includes the Arena, the Filarmonico, and a few explicitly
# named partner venues.  Do not infer Verona for an unknown touring venue.
VENUE_CITIES = {
    'arena di verona': 'Verona',
    'teatro romano di verona': 'Verona',
    'teatro filarmonico': 'Verona',
    'sala filarmonica': 'Verona',
    'accademia di agricoltura scienze e lettere': 'Verona',
    'museo nicolis': 'Villafranca di Verona',
    'museo degli affreschi': 'Verona',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def valid_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        return None


def city_for_venue(venue):
    folded = clean_text(venue).casefold()
    for marker, city in VENUE_CITIES.items():
        if marker in folded:
            return city
    return None


def calendar_records(session):
    soup = get_soup(session, CALENDAR_URL)
    records = []
    for day in soup.select('.day[data-day]'):
        event_date = valid_date(day.get('data-day'))
        if not event_date:
            continue
        for item in day.select('li.bh-calendarShow'):
            title_link = item.select_one('.heading a[href]')
            info = item.select('.info-icon .label')
            title = clean_text(title_link)
            venue = clean_text(info[0]) if info else ''
            city = city_for_venue(venue)
            if not title or not title_link or not venue or not city:
                continue
            time_from = None
            for label in info[1:]:
                match = re.fullmatch(r'(\d{1,2}):(\d{2})', clean_text(label))
                if match:
                    time_from = f'{int(match.group(1)):02d}:{match.group(2)}'
                    break
            records.append({
                'title': title,
                'date': event_date,
                'url': urljoin(SOURCE_URL, title_link.get('href')),
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': 'IT',
                'description': None,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def detail_description(session, url):
    soup = get_soup(session, url)
    parts = []

    brief_anchor = soup.select_one('#in-breve')
    brief_panel = brief_anchor.find_next(class_='panel-default') if brief_anchor else None
    if brief_panel:
        columns = brief_panel.select(':scope .row > .col-lg-6')
        if columns:
            summary = clean_text(columns[0].select_one('.txt'))
            if summary:
                parts.append(summary)
        facts = []
        for block in brief_panel.select('.info-icon'):
            label = clean_text(block.select_one('.label'))
            value = clean_text(block.select_one('.text'))
            if label and value and label.casefold() not in {'location', 'durata'}:
                facts.append(f'{label}: {value}')
        if facts:
            parts.append('\n'.join(facts))

    synopsis = soup.select_one('.bh-trama')
    if synopsis:
        synopsis_parts = []
        for act in synopsis.select('.bh-atto'):
            heading = clean_text(act.get('data-act-title'))
            body = clean_text(act)
            if body:
                synopsis_parts.append(f'{heading}\n{body}' if heading else body)
        if synopsis_parts:
            parts.append('Trama\n' + '\n\n'.join(synopsis_parts))
    return clean_text('\n\n'.join(parts)) or None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = calendar_records(session)
    urls = list(dict.fromkeys(record['url'] for record in records))
    descriptions = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(detail_description, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                descriptions[url] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Arena event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    for record in records:
        record['description'] = descriptions.get(record['url'])
    records = list({
        (record['title'], record['date'], record['time_from'], record['venue']): record
        for record in records
    }.values())
    return sorted(
        records,
        key=lambda row: (row['date'], row['time_from'] or '', row['title'], row['venue']),
    )


class ArenaItCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='arena_it',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='IT',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    ArenaItCrawler().run()


if __name__ == '__main__':
    main()
