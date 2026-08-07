import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.quatuors-luberon.org/new/index.php'
BASE_URL = 'https://www.quatuors-luberon.org/new/'
PROGRAMME_URL = urljoin(BASE_URL, 'programme.php?lg=FR')
EDITIONS_URL = urljoin(BASE_URL, 'editions.php?lg=FR')
SOURCE = 'Festival de Quatuors du Luberon'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9',
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


def programme_pages(session):
    """Return the live programme and all past editions available as HTML."""
    pages = [PROGRAMME_URL]
    soup = get_soup(session, EDITIONS_URL)
    for anchor in soup.select('a[href*="detail_edition.php"]'):
        pages.append(urljoin(BASE_URL, anchor.get('href')))
    return list(dict.fromkeys(pages))


def parse_date_time(text):
    match = re.search(
        r'\b(\d{2})[.\-/](\d{2})[.\-/](\d{4})\b'
        r'(?:\s*\|\s*(\d{1,2}):(\d{2}))?',
        clean_text(text),
    )
    if not match:
        return None, None
    try:
        event_date = datetime.strptime(
            '-'.join(match.group(1, 2, 3)), '%d-%m-%Y'
        ).date().isoformat()
    except ValueError:
        return None, None
    event_time = None
    if match.group(4):
        hour, minute = int(match.group(4)), int(match.group(5))
        if hour < 24 and minute < 60:
            event_time = f'{hour:02d}:{minute:02d}'
    return event_date, event_time


def parse_location(text):
    location = clean_text(text)
    folded = location.casefold()
    # Two archived outreach events publish a full address instead of the usual
    # "CITY, Venue" format. Keep the named venue and derive only the explicit
    # city, rather than allowing the address to leak into either field.
    if 'espace les romarins' in folded and 'apt' in folded:
        return 'Apt', 'Espace Les Romarins'
    if 'auditorium' in folded and 'diath' in folded and 'roque' in folded:
        return "La Roque-d'Anthéron", 'Auditorium de la Médiathèque'
    # Programme cards consistently publish "CITY, Venue". Splitting once keeps
    # any commas that legitimately form part of the venue name.
    parts = [part.strip() for part in location.split(',', 1)]
    if len(parts) != 2 or not all(parts):
        return None, None
    return parts[0].title(), parts[1]


def listing_items(session):
    items = []
    for page_url in programme_pages(session):
        try:
            soup = get_soup(session, page_url)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape programme page',
                event='crawler_page_failed',
                level='warning',
                url=page_url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        for card in soup.select('.cbp-item'):
            info = card.select_one('div[style*="background-color"]')
            detail = card.select_one('a[href*="projet.php?projet="]')
            if not info or not detail:
                continue
            fields = info.find_all('div', recursive=False)
            if len(fields) < 3:
                continue
            event_date, event_time = parse_date_time(fields[1])
            city, venue = parse_location(fields[2])
            title = clean_text(fields[0])
            if not title or not event_date or not city or not venue:
                continue
            items.append({
                'title': title,
                'date': event_date,
                'time_from': event_time,
                'city': city,
                'venue': venue,
                'url': urljoin(BASE_URL, detail.get('href')),
                'listing_description': clean_text(fields[3]) if len(fields) > 3 else '',
            })
    return items


def detail_description(session, item):
    soup = get_soup(session, item['url'])
    content = soup.select_one('.col50.right div[style*="color"]')
    detail = clean_text(content)
    parts = []
    if item['listing_description']:
        parts.append(item['listing_description'])
    if detail and detail not in parts:
        parts.append(detail)
    return '\n\n'.join(parts) or None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = listing_items(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(detail_description, session, item): item
            for item in items
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                description = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                description = item['listing_description'] or None
            records.append({
                'title': item['title'],
                'date': item['date'],
                'url': item['url'],
                'time_from': item['time_from'],
                'venue': item['venue'],
                'city': item['city'],
                'country_code': 'FR',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return sorted(records, key=lambda row: (row['date'], row['time_from'] or '', row['title']))


class QuatuorsLuberonOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='quatuors_luberon_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    QuatuorsLuberonOrgCrawler().run()


if __name__ == '__main__':
    main()
