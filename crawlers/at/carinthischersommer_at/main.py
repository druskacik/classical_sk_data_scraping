import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://carinthischersommer.at/'
PROGRAMME_URL = f'{SOURCE_URL}programm/'
SOURCE = 'Carinthischer Sommer'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'jänner': 1, 'januar': 1, 'februar': 2, 'märz': 3, 'april': 4,
    'mai': 5, 'juni': 6, 'juli': 7, 'aug': 8, 'august': 8, 'sept': 9,
    'september': 9, 'okt': 10, 'oktober': 10, 'nov': 11,
    'november': 11, 'dez': 12, 'dezember': 12,
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value, year):
    match = re.search(r'(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)', value)
    if not match:
        return None
    month = MONTHS.get(match.group(2).lower().rstrip('.'))
    if not month:
        return None
    try:
        return date(year, month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def resolve_city(venue):
    normalized = venue.lower()
    if 'klagenfurt' in normalized or 'klagenfurter' in normalized:
        return 'Klagenfurt am Wörthersee'
    if 'villach' in normalized or 'warmbad' in normalized:
        return 'Villach'
    if 'ossiach' in normalized:
        return 'Ossiach'
    if 'finkenstein' in normalized:
        return 'Finkenstein am Faaker See'
    if 'domenig steinhaus' in normalized:
        return 'Steindorf am Ossiacher See'
    return None


def listing_items(session):
    response = session.get(PROGRAMME_URL, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    page_text = clean_text(soup)
    year_match = re.search(r'Carinthische(?:r|n) Sommer(?:s)?\s+(20\d{2})', page_text, re.I)
    if not year_match:
        year_match = re.search(r'/wp-content/uploads/(20\d{2})/', response.text)
    if not year_match:
        raise ValueError('Could not determine the programme year')
    year = int(year_match.group(1))

    items = []
    for article in soup.select('article.cs-programm-item:not(.cs-programm-item--related)'):
        link = article.select_one('a.cs-programm-main[href]')
        title = clean_text(article.select_one('.cs-programm-titel'))
        venue = clean_text(article.select_one('.cs-programm-ort'))
        day = clean_text(article.select_one('.cs-programm-day'))
        time_text = clean_text(article.select_one('.cs-programm-zeit'))
        event_date = parse_date(day, year)
        time_match = re.search(r'\b(\d{1,2}):(\d{2})\b', time_text)
        city = resolve_city(venue)
        url = link.get('href', '').strip() if link else ''
        if not title or not event_date or not url or not venue or not city:
            continue
        items.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': (
                f'{int(time_match.group(1)):02d}:{time_match.group(2)}'
                if time_match else None
            ),
            'venue': venue,
            'city': city,
            'country_code': 'AT',
            'description': clean_text(article.select_one('.cs-programm-desc')) or None,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return items


def detail_description(session, record):
    response = session.get(record['url'], timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    description = clean_text(soup.select_one('.cs-event-detail-content'))
    return description or record['description']


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = listing_items(session)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(detail_description, session, record): record
            for record in records
        }
        for future in as_completed(futures):
            record = futures[future]
            try:
                record['description'] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Carinthischer Sommer event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=record['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class CarinthischerSommerAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='carinthischersommer_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='potential',
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
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    CarinthischerSommerAtCrawler().run()


if __name__ == '__main__':
    main()
