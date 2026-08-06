import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.staatsoper-stuttgart.de/'
CALENDAR_URL = urljoin(SOURCE_URL, 'spielplan/kalender/')
SOURCE = 'Staatsoper Stuttgart'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = (text.replace('\xa0', ' ').replace('\u202f', ' ')
            .replace('\u200b', '').replace('\xad', ''))
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount('https://', HTTPAdapter(
        pool_connections=8,
        pool_maxsize=8,
        max_retries=Retry(
            total=3,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 502, 503, 504),
        ),
    ))
    return session


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def calendar_pages(session):
    soup = get_soup(session, CALENDAR_URL)
    urls = {CALENDAR_URL}
    for link in soup.select('a[href]'):
        url = urljoin(CALENDAR_URL, link.get('href', '')).split('?', 1)[0].split('#', 1)[0]
        if re.fullmatch(rf'{re.escape(CALENDAR_URL)}\d{{4}}-\d{{2}}/', url):
            urls.add(url)
    return soup, sorted(urls)


def parse_location(venue):
    venue = clean_text(venue)
    if not venue:
        return None, None

    # Tour dates identify their city after a comma (for example,
    # "Beethovenhalle Bonn, Großer Saal" is handled by the explicit venue map).
    known_tour_venues = {
        'Beethovenhalle Bonn': 'Bonn',
        'Rätsche': 'Geislingen an der Steige',
    }
    for marker, city in known_tour_venues.items():
        if marker.casefold() in venue.casefold():
            return city, venue

    if ',' in venue:
        first, rest = (part.strip() for part in venue.split(',', 1))
        if rest and re.search(r'\b(?:Stuttgart|Bonn|Geislingen|Ludwigsburg|Esslingen)\b', rest):
            return rest, first

    # Unqualified halls in this institution's calendar are its Stuttgart
    # venues. Touring performances are qualified with the outside venue/city.
    return 'Stuttgart', venue


def card_description(card):
    parts = []
    subtitle = clean_text(card.select_one('.performance__subtitle'))
    if subtitle:
        parts.append(subtitle)
    for node in card.select('.performance__textcontent .performance__textitem'):
        value = clean_text(node)
        if value and value not in parts:
            parts.append(value)
    return '\n'.join(parts) or None


def parse_card(card):
    link = card.select_one('.performance__headline a[href]')
    title = clean_text(card.select_one('.performance__headline [itemprop="name"]'))
    start = card.select_one('meta[itemprop="startDate"][content]')
    textitems = card.select('.performance__textcontent .performance__textitem')
    venue_text = clean_text(textitems[-1]) if textitems else ''
    city, venue = parse_location(venue_text)
    if not link or not title or not start or not city or not venue:
        return None
    try:
        moment = datetime.fromisoformat(start['content'])
    except (TypeError, ValueError):
        return None
    event_url = urljoin(CALENDAR_URL, link['href'])
    event_url = re.sub(r'(/spielplan/kalender/)\d{4}-\d{2}/', r'\1', event_url)
    return {
        'title': title,
        'date': moment.date().isoformat(),
        'url': event_url,
        'time_from': moment.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'DE',
        'description': card_description(card),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def parse_page(soup):
    return [record for card in soup.select('.performance') if (record := parse_card(card))]


def detail_description(session, record):
    soup = get_soup(session, record['url'])
    parts = []
    for selector in (
        '.production__header',
        '.production__left',
        '.production-cast',
        '.production__cast',
    ):
        for node in soup.select(selector):
            value = clean_text(node)
            if value and value not in parts:
                parts.append(value)
    detail = '\n\n'.join(parts)
    if detail:
        summary = record.get('description')
        record['description'] = '\n\n'.join(part for part in (summary, detail) if part)
    return record


def get_concerts():
    session = make_session()
    first_soup, page_urls = calendar_pages(session)
    records = parse_page(first_soup)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(get_soup, session, url): url
            for url in page_urls if url != CALENDAR_URL
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_page(future.result()))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Staatsoper Stuttgart calendar page',
                    event='crawler_page_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    unique = {
        (record['url'], record['date'], record['time_from'], record['venue']): record
        for record in records
    }
    records = list(unique.values())

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(detail_description, session, record): record for record in records}
        enriched = []
        for future in as_completed(futures):
            record = futures[future]
            try:
                enriched.append(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Staatsoper Stuttgart event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=record['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                enriched.append(record)

    return sorted(enriched, key=lambda item: (
        item['date'], item['time_from'] or '', item['city'], item['title'], item['url']
    ))


class StaatsoperStuttgartDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='staatsoper_stuttgart_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    StaatsoperStuttgartDeCrawler().run()


if __name__ == '__main__':
    main()
