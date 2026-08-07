import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.symphonikerhamburg.de/'
SOURCE = 'Symphoniker Hamburg'
PROGRAM_URL = urljoin(SOURCE_URL, 'konzerte/')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}

# The orchestra tours occasionally. These names keep tour performances out of
# the Hamburg default while covering the locations used in the published archive.
CITY_MARKERS = {
    'hamburg': ('Hamburg', 'DE'),
    'lübeck': ('Lübeck', 'DE'),
    'lüneburg': ('Lüneburg', 'DE'),
    'hoyerswerda': ('Hoyerswerda', 'DE'),
    'cunewalde': ('Cunewalde', 'DE'),
    'pinneberg': ('Pinneberg', 'DE'),
    'elmshorn': ('Elmshorn', 'DE'),
    'großröhrsdorf': ('Großröhrsdorf', 'DE'),
    'meppen': ('Meppen', 'DE'),
    'duisburg': ('Duisburg', 'DE'),
    'schenefeld': ('Schenefeld', 'DE'),
    'görlitz': ('Görlitz', 'DE'),
    'zittau': ('Zittau', 'DE'),
    'weißwasser': ('Weißwasser', 'DE'),
    'kiel': ('Kiel', 'DE'),
    'bremen': ('Bremen', 'DE'),
    'hannover': ('Hannover', 'DE'),
    'berlin': ('Berlin', 'DE'),
    'münchen': ('München', 'DE'),
    'köln': ('Köln', 'DE'),
    'düsseldorf': ('Düsseldorf', 'DE'),
    'wiesbaden': ('Wiesbaden', 'DE'),
    'amsterdam': ('Amsterdam', 'NL'),
    'zgorzelec': ('Zgorzelec', 'PL'),
    'vilnius': ('Vilnius', 'LT'),
    'salzburg': ('Salzburg', 'AT'),
}

HAMBURG_VENUE_MARKERS = {
    'laeiszhalle',
    'elbphilharmonie',
    'brahms-foyer',
    'museum für kunst und gewerbe',
    'hauptkirche st. michaelis',
    'st. michaelis',
    'kampnagel',
    'fabrik',
    'hochschule für musik und theater',
    'friedrich-ebert-halle',
    'café international',
    'wälderhaus wilhelmsburg',
    'hanseatisches oberlandesgericht',
    'ernst deutsch theater',
    'planten un blomen',
    'römischer garten',
    'hauptkirche st. jacobi',
    'st. jacobi',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def catalogue_urls(session):
    soup = get_soup(session, PROGRAM_URL)
    urls = {PROGRAM_URL}
    for link in soup.select('a[href*="/konzerte/archiv/"]'):
        url = urljoin(SOURCE_URL, link.get('href', ''))
        if re.search(r'/konzerte/archiv/\d{4}-\d{4}/?$', urlparse(url).path):
            urls.add(url)
    return sorted(urls)


def detail_urls(session):
    urls = set()
    for catalogue_url in catalogue_urls(session):
        soup = get_soup(session, catalogue_url)
        for link in soup.select('a[href*="/konzerte/"]'):
            url = urljoin(SOURCE_URL, link.get('href', '')).split('#', 1)[0]
            path = urlparse(url).path.rstrip('/')
            if re.fullmatch(r'/konzerte/[^/]+-\d+', path):
                urls.add(url)
    return sorted(urls)


def resolve_location(venue):
    folded = venue.casefold()
    for marker, location in CITY_MARKERS.items():
        if marker in folded:
            return location
    if any(marker in folded for marker in HAMBURG_VENUE_MARKERS):
        return 'Hamburg', 'DE'
    return None


def parse_performance(value):
    match = re.search(
        r'(?P<date>\d{1,2}\.\d{1,2}\.\d{4})(?:.*?(?P<time>\d{1,2}:\d{2})\s*Uhr)?',
        value,
        re.DOTALL,
    )
    if not match:
        return None
    try:
        date = datetime.strptime(match.group('date'), '%d.%m.%Y').date().isoformat()
    except ValueError:
        return None
    return date, match.group('time')


def parse_detail(session, url):
    soup = get_soup(session, url)
    detail = soup.select_one('.konzertDetails')
    if not detail:
        return []

    heading = detail.find('h1')
    concert_type = detail.select_one('.kTyp')
    venue_node = detail.select_one('.location')
    heading_text = clean_text(heading.get_text(' ', strip=True) if heading else '')
    type_text = clean_text(concert_type.get_text(' ', strip=True) if concert_type else '')
    venue = clean_text(venue_node.get_text(' ', strip=True) if venue_node else '')
    location = resolve_location(venue)
    if not heading_text or not venue or not location:
        return []
    city, country_code = location

    title = heading_text
    if type_text and type_text.casefold() not in heading_text.casefold():
        title = f'{type_text} – {heading_text}'

    body = detail.select_one('.row > .col-sm-8')
    description = clean_text(body.get_text('\n', strip=True) if body else '') or None
    if re.search(r'\b(abgesagt|entfällt)\b', f'{type_text} {heading_text}', re.IGNORECASE):
        return []

    records = []
    for date_node in detail.select('.kDates .kDate'):
        performance = parse_performance(date_node.get_text(' ', strip=True))
        if not performance:
            continue
        date, time_from = performance
        records.append(
            {
                'title': title,
                'date': date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': country_code,
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = detail_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(parse_detail, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    unique_records = {}
    for record in records:
        key = (record['title'], record['date'], record['time_from'], record['venue'])
        unique_records[key] = record
    return sorted(
        unique_records.values(),
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class SymphonikerHamburgDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='symphonikerhamburg_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
    SymphonikerHamburgDeCrawler().run()


if __name__ == '__main__':
    main()
