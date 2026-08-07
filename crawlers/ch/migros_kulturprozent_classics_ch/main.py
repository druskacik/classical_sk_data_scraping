import re
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://migros-kulturprozent-classics.ch/'
OVERVIEW_URL = f'{SOURCE_URL}de/tickets/alle-orte/'
SITEMAP_URL = f'{SOURCE_URL}sitemap'
SOURCE = 'Migros-Kulturprozent-Classics'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'JANUAR': 1,
    'FEBRUAR': 2,
    'MÄRZ': 3,
    'APRIL': 4,
    'MAI': 5,
    'JUNI': 6,
    'JULI': 7,
    'AUGUST': 8,
    'SEPTEMBER': 9,
    'OKTOBER': 10,
    'NOVEMBER': 11,
    'DEZEMBER': 12,
}

VENUE_CITIES = {
    'VICTORIA HALL GENÈVE': ('Victoria Hall Genève', 'Genève'),
    'TONHALLE ZÜRICH': ('Tonhalle Zürich', 'Zürich'),
    'KKL LUZERN': ('KKL Luzern', 'Luzern'),
    'CASINO BERN': ('Casino Bern', 'Bern'),
    'NODA BCVS SION': ('Noda BCVS', 'Sion'),
}

PERFORMANCE_RE = re.compile(
    r'^\s*(?P<venue>.+?)\s*·\s*'
    r'(?P<day>\d{1,2})\.\s*(?P<month>[A-ZÄÖÜ]+)\s+(?P<year>\d{4})\s*·\s*'
    r'(?P<hour>\d{1,2}):(?P<minute>\d{2})\s+UHR\s*$',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response


def is_event_url(url):
    path = urlparse(url).path.rstrip('/')
    return path.startswith('/de/tickets/alle-orte/') and path.count('/') == 4


def event_urls(session):
    """Combine the current overview with the CMS sitemap.

    The sitemap can lag behind newly published events, while the overview has
    occasionally omitted older but still live detail pages.
    """
    urls = []
    overview = BeautifulSoup(get_response(session, OVERVIEW_URL).text, 'html.parser')
    urls.extend(
        urljoin(OVERVIEW_URL, link['href'])
        for link in overview.select('a[href]')
        if is_event_url(urljoin(OVERVIEW_URL, link['href']))
    )

    sitemap = BeautifulSoup(get_response(session, SITEMAP_URL).text, 'xml')
    for node in sitemap.select('loc'):
        url = clean_text(node)
        path = urlparse(url).path.rstrip('/')
        if path.startswith('/tickets/alle-orte/') and path.count('/') == 3:
            slug = path.rsplit('/', 1)[-1]
            urls.append(urljoin(OVERVIEW_URL, slug))
    return list(dict.fromkeys(urls))


def parse_performance(value):
    match = PERFORMANCE_RE.match(clean_text(value).upper())
    if not match:
        return None
    venue_key = match.group('venue').strip()
    venue_city = VENUE_CITIES.get(venue_key)
    month = MONTHS.get(match.group('month'))
    if not venue_city or not month:
        return None
    try:
        event_date = date(
            int(match.group('year')), month, int(match.group('day'))
        ).isoformat()
        hour = int(match.group('hour'))
        minute = int(match.group('minute'))
        if hour > 23 or minute > 59:
            return None
    except ValueError:
        return None
    venue, city = venue_city
    return event_date, f'{hour:02d}:{minute:02d}', venue, city


def make_records(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.select_one('o-body')
    title = clean_text(body.select_one('h1')) if body else ''
    if not title or not body:
        return []

    # The body includes the editorial introduction, artists, and full programme.
    # This is intentionally broad so later programme extraction sees every work.
    description = clean_text(body) or None
    records = []
    for heading in body.select('h6'):
        performance = parse_performance(heading)
        if not performance:
            continue
        event_date, time_from, venue, city = performance
        records.append(
            {
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
        )
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []
    for url in urls:
        try:
            response = get_response(session, url)
            records.extend(make_records(response.url, response.text))
        except requests.RequestException as error:
            log_message(
                'Failed to scrape Migros Classics event',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )

    log_message(
        'Migros Classics calendar parsed',
        event='crawler_scrape_completed',
        url=OVERVIEW_URL,
        record_count=len(records),
    )
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class MigrosKulturprozentClassicsChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='migros_kulturprozent_classics_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
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
    MigrosKulturprozentClassicsChCrawler().run()


if __name__ == '__main__':
    main()
