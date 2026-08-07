import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.festivalpablocasals.fr/'
EVENTS_API = f'{SOURCE_URL}wp-json/wp/v2/evenement'
SOURCE = 'Festival Pablo Casals de Prades'
NON_MUSIC_TERM_IDS = {48}  # Conferences and exhibitions.

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
}

MONTHS = {
    'janvier': 1,
    'février': 2,
    'fevrier': 2,
    'mars': 3,
    'avril': 4,
    'mai': 5,
    'juin': 6,
    'juillet': 7,
    'août': 8,
    'aout': 8,
    'septembre': 9,
    'octobre': 10,
    'novembre': 11,
    'décembre': 12,
    'decembre': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def listing_events(session):
    # The public WordPress collection includes every event retained by the site,
    # including past events, and exposes at most 100 records per page.
    page = 1
    events = []
    while True:
        batch = get_json(
            session,
            EVENTS_API,
            params={
                'per_page': 100,
                'page': page,
                'orderby': 'id',
                'order': 'asc',
            },
        )
        events.extend(batch)
        if len(batch) < 100:
            return events
        page += 1


def parse_date(value):
    match = re.search(
        r'\b(\d{1,2})\s+([a-zéûôîàèùç]+)\s+(\d{4})\b',
        clean_text(value).lower(),
    )
    if not match:
        return None
    month = MONTHS.get(match.group(2))
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2})\s*h\s*(\d{2})\b', clean_text(value), re.IGNORECASE)
    if not match:
        return None
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def widget_text(soup, data_id):
    node = soup.select_one(f'[data-id="{data_id}"]')
    return clean_text(node.get_text('\n', strip=True)) if node else ''


def resolve_city(address):
    # Event addresses consistently end with a French postcode and locality.
    match = re.search(r'\b\d{5}\s+([^\n,]+)', address)
    return clean_text(match.group(1)).strip(' .') if match else None


def detail_record(session, event):
    url = event.get('link') or ''
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    title_node = soup.select_one('h1.elementor-heading-title')
    title = clean_text(title_node.get_text(' ', strip=True)) if title_node else ''
    venue = widget_text(soup, 'a368946')
    event_date = parse_date(widget_text(soup, 'bb0d5cf'))
    time_from = parse_time(widget_text(soup, '5d91138'))
    address = widget_text(soup, 'b588c52')
    city = resolve_city(address)

    if not title or not event_date or not url or not venue or not city:
        return None

    description_parts = []
    api_description = clean_text((event.get('content') or {}).get('rendered'))
    if api_description:
        description_parts.append(api_description)
    for data_id in ('03d779e', '2bc03f4'):
        value = widget_text(soup, data_id)
        if value and value not in description_parts:
            description_parts.append(value)

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'FR',
        'description': clean_text('\n\n'.join(description_parts)) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    for event in listing_events(session):
        if NON_MUSIC_TERM_IDS.intersection(event.get('type-d-evenement') or []):
            continue
        try:
            record = detail_record(session, event)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape concert detail',
                event='crawler_item_failed',
                level='warning',
                url=event.get('link'),
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        if record:
            records.append(record)

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class FestivalPabloCasalsFrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='festivalpablocasals_fr',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
        # The festival also publishes club, young-public, and choral events;
        # route the mixed programme through the classifier.
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
    FestivalPabloCasalsFrCrawler().run()


if __name__ == '__main__':
    main()
