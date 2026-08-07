import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://philharmonique.strasbourg.eu/'
AGENDA_URL = urljoin(SOURCE_URL, 'agenda')
SOURCE = 'Orchestre Philharmonique de Strasbourg'
DEFAULT_CITY = 'Strasbourg'
DEFAULT_VENUE = 'Palais de la musique et des congrès'

VENUE_LOCATIONS = {
    'Arsenal': ('Metz', 'FR'),
    'Basilique Saint-Denis': ('Saint-Denis', 'FR'),
    'Bristol Beacon': ('Bristol', 'GB'),
    'Cadogan Hall': ('Londres', 'GB'),
    'Forum am Schlosspark': ('Ludwigsburg', 'DE'),
    'La Filature': ('Mulhouse', 'FR'),
    'Oberrheinhalle': ('Offenburg', 'DE'),
    'Royal Concert Hall': ('Nottingham', 'GB'),
    'Salle de musique': ('La Chaux-de-Fonds', 'CH'),
    'Symphony Hall': ('Birmingham', 'GB'),
    'The Anvil': ('Basingstoke', 'GB'),
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9',
}

MONTHS = {
    'janvier': 1, 'janv': 1, 'février': 2, 'févr': 2, 'mars': 3,
    'avril': 4, 'avr': 4, 'mai': 5, 'juin': 6, 'juillet': 7,
    'juil': 7, 'août': 8, 'septembre': 9, 'sept': 9, 'octobre': 10,
    'oct': 10, 'novembre': 11, 'nov': 11, 'décembre': 12, 'déc': 12,
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
    return BeautifulSoup(response.text, 'html.parser')


def parse_day_month(text):
    matches = re.findall(r'(\d{1,2})\s+([a-zéû\.]+)', text.lower())
    values = []
    for day_text, month_text in matches:
        month = MONTHS.get(month_text.rstrip('.'))
        if month:
            values.append((int(day_text), month))
    return values


def listing_items(session):
    soup = get_soup(session, AGENDA_URL)
    pages = [AGENDA_URL]
    for anchor in soup.select('a[href]'):
        href = anchor.get('href', '')
        if '_SearchAssetPortlet_cur=' in href:
            pages.append(urljoin(SOURCE_URL, href))

    cards = []
    seen = set()
    for page_url in dict.fromkeys(pages):
        page = soup if page_url == AGENDA_URL else get_soup(session, page_url)
        for card in page.select('a.ops-card-concert[href*="detail-evenement"]'):
            url = urljoin(SOURCE_URL, card.get('href'))
            if url in seen:
                continue
            seen.add(url)
            cards.append({
                'url': url,
                'date_text': clean_text(card.select_one('time')),
                'kind': clean_text(card.select_one('.ops-typologie')),
            })

    # Results are chronologically ordered but omit the year. Anchor the first
    # result to today and advance the year whenever the month wraps to January.
    current = date.today()
    year = current.year
    previous_month = current.month
    for card in cards:
        dates = parse_day_month(card['date_text'])
        if not dates:
            continue
        month = dates[0][1]
        if month < previous_month and previous_month - month > 6:
            year += 1
        card['year'] = year
        previous_month = month
    return cards


def infer_location(title, kind, venue):
    if venue in VENUE_LOCATIONS:
        return VENUE_LOCATIONS[venue]
    match = re.search(
        r'\s(?:à|au|aux)\s+([^|–—,:()]+)', title, re.IGNORECASE,
    )
    if match:
        return clean_text(match.group(1)), 'FR'
    if 'décentralisé' in kind.lower():
        return None, None
    return DEFAULT_CITY, 'FR'


def description_from(soup):
    parts = []
    info = soup.select_one('.ops-bloc-infos-concert')
    description = soup.select_one('.ops-bloc-description .ops-content-wrapper')
    for element in (info, description):
        text = clean_text(element)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_time(text):
    match = re.search(r'(\d{1,2})h(?:(\d{2}))?', text)
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2) or "00"}'


def parse_detail(session, item):
    soup = get_soup(session, item['url'])
    title = clean_text(soup.select_one('.ops-header-concert h1'))
    if not title:
        title = clean_text(soup.select_one('.ops-item h3'))
    venue = clean_text(soup.select_one('.ops-bloc-infos-concert address')) or DEFAULT_VENUE
    city, country_code = infer_location(title, item['kind'], venue)
    if not title or not city:
        return []

    performances = soup.select('#ops-representations .ops-item') or soup.select('.ops-item')
    records = []
    start_dates = parse_day_month(item['date_text'])
    start_month = start_dates[0][1] if start_dates else None
    for performance in performances:
        values = parse_day_month(clean_text(performance.select_one('time')))
        if not values:
            continue
        day, month = values[0]
        year = item.get('year', date.today().year)
        if start_month and start_month >= 7 and month < start_month:
            year += 1
        try:
            event_date = date(year, month, day).isoformat()
        except ValueError:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': item['url'],
            'time_from': parse_time(clean_text(performance.select_one('.ops-horaires'))),
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': description_from(soup),
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = listing_items(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(parse_detail, session, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(records, key=lambda row: (row['date'], row['time_from'] or '', row['title']))


class PhilharmoniqueStrasbourgEuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='philharmonique_strasbourg_eu',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
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
    PhilharmoniqueStrasbourgEuCrawler().run()


if __name__ == '__main__':
    main()
