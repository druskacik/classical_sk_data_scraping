import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://ofo.no/'
SOURCE = 'Oslo-filharmonien'
SITEMAP_URL = f'{SOURCE_URL}sitemap.xml'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'nb-NO,nb;q=0.9,en;q=0.7',
}

MONTHS = {
    'januar': 1, 'jan': 1, 'februar': 2, 'feb': 2, 'mars': 3, 'mar': 3,
    'april': 4, 'apr': 4, 'mai': 5, 'juni': 6, 'jun': 6, 'juli': 7,
    'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9,
    'oktober': 10, 'okt': 10, 'november': 11, 'nov': 11,
    'desember': 12, 'des': 12,
    'january': 1, 'february': 2, 'march': 3, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10,
    'november': 11, 'december': 12,
}

# Explicit touring venues must never inherit the orchestra's Oslo default.
TOUR_VENUES = {
    'royal albert hall': ('Royal Albert Hall', 'London', 'GB'),
    'barbican': ('Barbican Centre', 'London', 'GB'),
    'concertgebouw': ('Concertgebouw', 'Amsterdam', 'NL'),
    'elbphilharmonie': ('Elbphilharmonie', 'Hamburg', 'DE'),
    'berliner philharmonie': ('Berliner Philharmonie', 'Berlin', 'DE'),
    'philharmonie de paris': ('Philharmonie de Paris', 'Paris', 'FR'),
    'musikverein': ('Musikverein', 'Vienna', 'AT'),
    'konzerthaus wien': ('Wiener Konzerthaus', 'Vienna', 'AT'),
    'kkl luzern': ('KKL Luzern', 'Lucerne', 'CH'),
    'suntory hall': ('Suntory Hall', 'Tokyo', 'JP'),
    'carnegie hall': ('Carnegie Hall', 'New York', 'US'),
}

OSLO_MARKERS = (
    'oslo', 'sentralen', 'slottsplassen', 'musikkrom', 'lindemansalen',
    'norges musikkhøgskole', 'nmh', 'munch', 'nasjonalmuseet',
    'universitetets aula', 'nationaltheatret', 'deichman',
    'unge kunstneres samfund', 'uks', 'ridehuset', 'rockefeller',
)


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
    return BeautifulSoup(response.text, 'xml')


def concert_urls(session):
    index = get_soup(session, SITEMAP_URL)
    sitemap_urls = [
        clean_text(loc) for loc in index.find_all('loc')
        if 'sitemap-concerts-' in clean_text(loc)
    ]
    urls = []
    for sitemap_url in sitemap_urls:
        sitemap = get_soup(session, sitemap_url)
        urls.extend(
            clean_text(loc) for loc in sitemap.find_all('loc')
            if '/no/konserter/' in clean_text(loc)
        )
    return list(dict.fromkeys(urls))


def resolve_location(value):
    venue = clean_text(value)
    normalized = venue.casefold()
    if not venue:
        return None
    for marker, result in TOUR_VENUES.items():
        if marker in normalized:
            return result
    if any(marker in normalized for marker in OSLO_MARKERS):
        return venue, 'Oslo', 'NO'
    # Unqualified room names on OFO pages refer to rooms in Oslo Konserthus.
    if normalized in {'store sal', 'lille sal', 'glasshuset'}:
        return venue, 'Oslo', 'NO'
    return None


def parse_performance(value, base_date):
    text = clean_text(value).lower()
    match = re.search(
        r'(\d{1,2})\s*[.–-]?\s*(' + '|'.join(MONTHS) + r')'
        r'(?:\s+(\d{4}))?.*?(?:kl\s*|at\s*|[–—-]\s*)'
        r'(\d{1,2})[:.]([0-5]\d)(?:\s*(am|pm))?',
        text,
    )
    if not match:
        return None
    month = MONTHS[match.group(2)]
    year = int(match.group(3)) if match.group(3) else base_date.year
    if not match.group(3) and month < base_date.month - 6:
        year += 1
    elif not match.group(3) and month > base_date.month + 6:
        year -= 1
    try:
        event_date = date(year, month, int(match.group(1))).isoformat()
    except ValueError:
        return None
    hour = int(match.group(4))
    meridiem = match.group(6)
    if meridiem == 'pm' and hour < 12:
        hour += 12
    elif meridiem == 'am' and hour == 12:
        hour = 0
    if hour > 23:
        return None
    return event_date, f'{hour:02d}:{match.group(5)}'


def descriptions(main, title):
    parts = []
    for node in main.select('.wysiwyg'):
        value = clean_text(node)
        if value and value not in parts:
            parts.append(value)

    for heading in main.find_all(['h2', 'h3'], string=re.compile(r'Hva spilles', re.I)):
        works = heading.find_next('ul')
        if works:
            value = clean_text(works)
            if value:
                parts.append('Hva spilles\n' + value)
        break
    description = clean_text('\n\n'.join(parts))
    return description if description and description != title else None


def parse_concert(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    main = soup.find('main')
    if not main:
        return []

    title_node = main.find('h2')
    title = clean_text(title_node)
    if not title:
        page_title = clean_text(soup.title)
        title = re.sub(r'\s*\|\s*Oslo-filharmonien\s*$', '', page_title)

    path_date = re.search(r'/konserter/(\d{4}-\d{2}-\d{2})/', url)
    first_time = next(
        (node for node in main.find_all('time') if re.search(r'\b\d{4}\b', clean_text(node))),
        None,
    )
    if not title or not path_date or not first_time:
        return []
    try:
        base_date = date.fromisoformat(path_date.group(1))
    except ValueError:
        return []

    info = first_time.parent.parent
    venue_node = first_time.parent.find_next_sibling('span')
    location = resolve_location(venue_node)
    if not location:
        return []
    venue, city, country_code = location

    performance_values = []
    dropdown = info.select_one('[data-component="archive/Dropdown"]')
    if dropdown:
        selected = dropdown.select_one('a[href^="#"] span')
        if selected:
            performance_values.append((clean_text(selected), url))
        for link in dropdown.select('a[href*="?id="]'):
            performance_values.append((clean_text(link), urljoin(url, link.get('href'))))
    else:
        performance_values.append((clean_text(first_time), url))

    description = descriptions(main, title)
    records = []
    for value, performance_url in performance_values:
        parsed = parse_performance(value, base_date)
        if not parsed:
            # A single event page may omit its time while retaining a valid date.
            if len(performance_values) == 1:
                parsed = (base_date.isoformat(), None)
            else:
                continue
        event_date, time_from = parsed
        records.append({
            'title': title,
            'date': event_date,
            'url': performance_url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def fetch_concert(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return parse_concert(response.text, url)


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = concert_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_concert, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch OFO concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['venue']
    ))


class OfoNoCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ofo_no',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='NO',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from'],
    )

    def scrape(self):
        return get_concerts()


def main():
    OfoNoCrawler().run()


if __name__ == '__main__':
    main()
