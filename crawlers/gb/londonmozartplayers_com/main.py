import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.londonmozartplayers.com/'
UPCOMING_URL = urljoin(SOURCE_URL, 'wp-content/uploads/whats-on.html')
PAST_URL = urljoin(SOURCE_URL, 'whats-on/past-events/')
SOURCE = 'London Mozart Players'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# Detail pages often reduce regional locations to "UK" and overseas locations
# to "Europe". These mappings are limited to venues actually published by LMP.
VENUE_LOCATIONS = {
    'Alexandra Palace': ('London', 'GB'),
    'Cadogan Hall': ('London', 'GB'),
    'Canterbury Cathedral': ('Canterbury', 'GB'),
    'Centro Cultural de Belém, Pequeno Auditório': ('Lisbon', 'PT'),
    'Church of Christ the Cornerstone, Milton Keynes': ('Milton Keynes', 'GB'),
    'Church of Our Lady and the English Martyrs': ('Cambridge', 'GB'),
    'Croham Hurst Golf Club': ('Croydon', 'GB'),
    'Fairfield Halls': ('Croydon', 'GB'),
    'G Live, Guildford': ('Guildford', 'GB'),
    'Holywell Music Room, Oxford': ('Oxford', 'GB'),
    'Inner Temple Hall, London': ('London', 'GB'),
    'Jersey Town Church': ('Saint Helier', 'JE'),
    "King's Concert Hall": ('London', 'GB'),
    'Kings Place, Kings Cross': ('London', 'GB'),
    'Marlborough College Concert Series': ('Marlborough', 'GB'),
    'Memorial Hall, Uppingham School': ('Uppingham', 'GB'),
    'Merton Arts Space, Wimbledon Library': ('London', 'GB'),
    'Milton Court': ('London', 'GB'),
    'National Liberal Club': ('London', 'GB'),
    'Residence, Kaisersaal': ('Würzburg', 'DE'),
    'Royal Festival Hall, Southbank Centre': ('London', 'GB'),
    'Royal Holloway, University of London': ('Egham', 'GB'),
    'Saffron Hall': ('Saffron Walden', 'GB'),
    'Sheldonian Theatre, Oxford': ('Oxford', 'GB'),
    'Smith Square Hall': ('London', 'GB'),
    'Southwark Cathedral': ('London', 'GB'),
    'St Albans Cathedral': ('St Albans', 'GB'),
    "St John's, Upper Norwood": ('Croydon', 'GB'),
    "St Luke's, Grayshott": ('Grayshott', 'GB'),
    "St. Mary's Church, Woburn": ('Woburn', 'GB'),
    'St Mary the Virgin, Wotton-under-Edge': ('Wotton-under-Edge', 'GB'),
    'St Martin-in-the-Fields': ('London', 'GB'),
    'St. Martin-in-the-Fields': ('London', 'GB'),
    "St Paul's, Knightsbridge": ('London', 'GB'),
    'St Nicholas Church, Newbury': ('Newbury', 'GB'),
    'Teatro Cine de Torres Vedras': ('Torres Vedras', 'PT'),
    'Thaxted Parish Church': ('Thaxted', 'GB'),
    'The Apex, Bury St Edmunds': ('Bury St Edmunds', 'GB'),
    'The College Chapel, Kings College London': ('London', 'GB'),
    'The Grand Hotel, Eastbourne': ('Eastbourne', 'GB'),
    'The Great Hall, Dartington Hall': ('Totnes', 'GB'),
    'The Henrietta Barnett School': ('London', 'GB'),
    'Vinehall School, East Sussex': ('Robertsbridge', 'GB'),
    'Winchester Cathedral': ('Winchester', 'GB'),
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


def discover_event_urls(session):
    urls = set()
    for listing_url in (UPCOMING_URL, PAST_URL):
        soup = get_soup(session, listing_url)
        for anchor in soup.select('a[href*="/event/"]'):
            url = urljoin(SOURCE_URL, anchor.get('href', ''))
            if url.startswith(urljoin(SOURCE_URL, 'event/')):
                urls.add(url)
    return sorted(urls)


def parse_date(value):
    # Multi-day residencies are represented by their real first calendar date.
    value = re.sub(r'^(\d{1,2})\s*-\s*\d{1,2}\s+', r'\1 ', value.strip())
    try:
        return datetime.strptime(value, '%d %B %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', value, re.I)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 12 or minute > 59:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def resolve_location(venue, location_text):
    mapped = VENUE_LOCATIONS.get(venue)
    if mapped:
        return mapped

    parts = [part.strip() for part in location_text.split(',') if part.strip()]
    if 'London' in parts:
        return 'London', 'GB'
    if 'Croydon' in parts:
        return 'Croydon', 'GB'
    return None, None


def make_description(soup):
    parts = []
    programme = []
    for item in soup.select('.programme-item__content'):
        composer = clean_text(item.select_one('.credit'))
        work = clean_text(item.select_one('p'))
        line = f'{composer}: {work}' if composer and work else composer or work
        if line:
            programme.append(line)
    if programme:
        parts.append('Programme\n' + '\n'.join(programme))

    for selector in ('.main-content__intro', '.main-content__text'):
        text = clean_text(soup.select_one(selector))
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_event(soup, url):
    title = clean_text(soup.select_one('h1.page-header__heading'))
    details = [clean_text(item) for item in soup.select('.post-details--event > li')]
    if not title or len(details) < 3:
        return None

    event_date = parse_date(details[0])
    time_from = parse_time(details[1])
    venue_index = 2 if time_from else 1
    if len(details) <= venue_index:
        return None
    venue = details[venue_index]
    location_text = details[venue_index + 1] if len(details) > venue_index + 1 else ''
    city, country_code = resolve_location(venue, location_text)
    if not event_date or not venue or not city or not country_code:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': make_description(soup),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = discover_event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_event(future.result(), url)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape London Mozart Players event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
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


class LondonMozartPlayersComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='londonmozartplayers_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='classical',
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
    LondonMozartPlayersComCrawler().run()


if __name__ == '__main__':
    main()
