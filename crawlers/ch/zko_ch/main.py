import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://zko.ch/'
CALENDAR_URL = urljoin(SOURCE_URL, 'konzerte/')
SOURCE = 'Zürcher Kammerorchester'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        value = value.get_text('\n', strip=True)
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_urls(session):
    soup = get_soup(session, CALENDAR_URL)
    urls = {
        urljoin(CALENDAR_URL, link.get('href'))
        for link in soup.select('a[href*="/events/"]')
        if link.get('href')
    }
    return sorted(url for url in urls if url.startswith(urljoin(SOURCE_URL, 'events/')))


def labelled_value(soup, labels):
    wanted = {label.casefold() for label in labels}
    for item in soup.find_all('li'):
        label = item.find('span')
        if not label or clean_text(label).casefold() not in wanted:
            continue
        label.extract()
        return clean_text(item)
    return ''


def resolve_city(venue):
    folded = venue.casefold()
    city_markers = (
        ('einsiedeln', 'Einsiedeln'),
        ('winterthur', 'Winterthur'),
        ('baden', 'Baden'),
        ('basel', 'Basel'),
        ('bern', 'Bern'),
        ('luzern', 'Luzern'),
        ('st. gallen', 'St. Gallen'),
        ('zug', 'Zug'),
        ('zürich', 'Zürich'),
        ('zuerich', 'Zürich'),
    )
    for marker, city in city_markers:
        if marker in folded:
            return city

    # These are venue names used by the orchestra's Zurich calendar. An
    # explicitly named touring venue never receives this home-city fallback.
    zurich_venues = ('zko-haus', 'kunsthaus', 'tonhalle', 'maag halle', 'kirche neumünster')
    if any(marker in folded for marker in zurich_venues):
        return 'Zürich'
    return None


def parse_detail(session, url):
    soup = get_soup(session, url)
    title_node = soup.select_one('.caption-title')
    title = clean_text(title_node)
    date_text = labelled_value(soup, ('DATUM',))
    time_text = labelled_value(soup, ('UHRZEIT',))
    venue = labelled_value(soup, ('ADRESSE', 'Adresse')).split('\n', 1)[0]
    venue = re.sub(r'\s*,(?:\s*,)*\s*$', '', venue).strip()
    city = resolve_city(venue)

    performances = []
    for item in soup.find_all('li'):
        item_text = clean_text(item)
        match = re.search(
            r'\b(\d{1,2}\.\d{1,2}\.\d{4})\s*/\s*'
            r'([01]?\d|2[0-3])[:.]([0-5]\d)',
            item_text,
        )
        if match:
            performances.append((match.group(1), f'{int(match.group(2)):02d}:{match.group(3)}'))

    date_match = re.search(r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b', date_text)
    if not performances and date_match:
        start_times = re.findall(r'(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)', time_text)
        times = start_times[::2] if len(start_times) > 1 else start_times
        performances = [
            (date_match.group(1), f'{int(hour):02d}:{minute}')
            for hour, minute in times
        ] or [(date_match.group(1), None)]

    if not title or not performances or not venue or not city:
        return []

    description_node = soup.select_one('.contentEventRight')
    description = clean_text(description_node) or None
    records = []
    for raw_date, time_from in performances:
        try:
            event_date = datetime.strptime(raw_date, '%d.%m.%Y').date().isoformat()
        except ValueError:
            continue
        record = {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'CH',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        if record not in records:
            records.append(record)
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(parse_detail, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'] or '', record['title']),
    )


class ZkoChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='zko_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
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
    ZkoChCrawler().run()


if __name__ == '__main__':
    main()
