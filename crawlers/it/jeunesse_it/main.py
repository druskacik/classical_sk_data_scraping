import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.jeunesse.it/'
EVENTS_URL = urljoin(SOURCE_URL, 'elenco-eventi/')
SOURCE = 'Fondazione Gioventù Musicale Italia'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; ClassicalBot/1.0)',
    'Accept-Language': 'it-IT,it;q=0.9',
}
MAX_PAGES = 100

# Events are spread across many Italian branches. These names occur explicitly
# in the location strings (sometimes embedded in a venue name rather than after
# a comma), so they are safer than applying the foundation's Milan address.
CITIES = sorted({
    'Amandola', 'Angera', 'Arona', 'Calogna', 'Camerino',
    'Corigliano-Rossano', 'Civitella del Tronto', 'Fabriano', 'Fanano',
    'Fermo', 'Fiera di Primiero', 'Gavirate', 'Grottammare', 'Guastalla',
    'Lama Mocogno', 'Laveno Mombello', 'Leggiuno', 'Lesa', 'Milano',
    'Modena', 'Monsampolo del Tronto', 'Montechiarugolo',
    'Pavullo nel Frignano', 'Ripatransone', 'San Benedetto del Tronto',
    'Scandiano', 'Sesto Calende', 'Sestola', 'Todi', 'Tonadico',
    'Traversetolo',
}, key=len, reverse=True)


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value):
    try:
        return datetime.strptime(clean_text(value), '%d/%m/%Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'(?<!\d)(\d{1,2})[.:](\d{2})(?!\d)', clean_text(value))
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def parse_location(value):
    location = clean_text(value)
    if not location:
        return None, None
    city = next(
        (name for name in CITIES if re.search(rf'(?<!\w){re.escape(name)}(?!\w)', location, re.I)),
        None,
    )
    if not city and re.search(r'\bMusei di Amandola\b', location, re.I):
        city = 'Amandola'
    if not city:
        return None, None

    # Province abbreviations and street addresses help identify a place but are
    # not part of its venue name.
    venue = re.sub(r'\s*\((?:Via|Piazza|Viale|Corso)\s+[^)]*\)', '', location, flags=re.I)
    venue = re.sub(r'\s*,\s*Largo\s+[^,]+$', '', venue, flags=re.I)
    venue = re.sub(r'\s+h\.\s*\d{1,2}[.:]\d{2}\s*$', '', venue, flags=re.I)
    venue = re.sub(r'\s+', ' ', venue).strip(' ,-–')
    if venue.casefold() == city.casefold():
        return None, None
    return venue, city


def get_soup(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def catalogue_records(session):
    records = []
    seen_urls = set()
    for page in range(1, MAX_PAGES + 1):
        soup = get_soup(session, EVENTS_URL, {'pno': page})
        items = soup.select('.event-item')
        if not items:
            break
        new_urls = 0
        for item in items:
            link = item.select_one('a.event-link[href]')
            event_url = urljoin(SOURCE_URL, link.get('href')) if link else ''
            if not event_url or event_url in seen_urls:
                continue
            seen_urls.add(event_url)
            new_urls += 1
            title = clean_text(item.select_one('.event-title'))
            event_date = parse_date(item.select_one('.event-date'))
            venue, city = parse_location(item.select_one('.event-desc'))
            if not title or not event_date or not venue or not city:
                continue
            records.append({
                'title': title,
                'date': event_date,
                'url': event_url,
                'time_from': parse_time(item.select_one('.event-time')),
                'venue': venue,
                'city': city,
                'country_code': 'IT',
                'description': None,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
        if new_urls == 0:
            break
    return records


def detail_description(session, url):
    soup = get_soup(session, url)
    event = soup.select_one('.em-event-single')
    if not event:
        return None
    event = BeautifulSoup(str(event), 'html.parser')
    for element in event.select('script, style, nav, .sigle-event-title, .event-post-navigation'):
        element.decompose()
    text = clean_text(event)
    return text or None


class JeunesseItCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='jeunesse_it',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='IT',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        records = catalogue_records(session)
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(detail_description, session, record['url']): record
                for record in records
            }
            for future in as_completed(futures):
                record = futures[future]
                try:
                    record['description'] = future.result()
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape Jeunesse event detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=record['url'],
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(
            records,
            key=lambda row: (row['date'], row['time_from'] or '', row['title'], row['venue']),
        )


def main():
    JeunesseItCrawler().run()


if __name__ == '__main__':
    main()
