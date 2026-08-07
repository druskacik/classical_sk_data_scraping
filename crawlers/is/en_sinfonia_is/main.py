import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://en.sinfonia.is/'
CONCERTS_URL = urljoin(SOURCE_URL, 'concerts-tickets/')
SOURCE = 'Iceland Symphony Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

MONTHS = {
    name: number
    for number, name in enumerate(
        ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
         'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'),
        start=1,
    )
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_urls(soup):
    urls = set()
    for link in soup.select('.eventlist .item h3 > a[href]'):
        url = urljoin(CONCERTS_URL, link.get('href'))
        path = urlparse(url).path.rstrip('/')
        if path.count('/') == 2 and path.startswith('/concerts-tickets/'):
            urls.add(url)
    return urls


def discover_event_urls(session):
    # The main programme contains the complete announced future season.
    urls = listing_urls(get_soup(session, CONCERTS_URL))

    # Annual routes retain past programmes. Stop only after two consecutive
    # empty years so a single exceptional season cannot truncate discovery.
    empty_years = 0
    year = date.today().year
    while empty_years < 2:
        archive_url = urljoin(CONCERTS_URL, str(year))
        try:
            archive_urls = listing_urls(get_soup(session, archive_url))
        except requests.RequestException as error:
            log_message(
                'Failed to inspect concert archive',
                event='crawler_archive_failed',
                level='warning',
                url=archive_url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            archive_urls = set()
        urls.update(archive_urls)
        empty_years = 0 if archive_urls else empty_years + 1
        year -= 1

    return sorted(urls)


def parse_date(value):
    match = re.search(r'\b(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\b', value)
    if not match or match.group(2) not in MONTHS:
        return None
    try:
        return date(
            int(match.group(3)), MONTHS[match.group(2)], int(match.group(1))
        ).isoformat()
    except ValueError:
        return None


def resolve_city(venue):
    normalized = venue.casefold()
    if 'harpa' in normalized or 'háskólabíó' in normalized:
        return 'Reykjavík'
    if 'hof' in normalized and 'akureyri' in normalized:
        return 'Akureyri'
    if 'salurinn' in normalized and 'kópavog' in normalized:
        return 'Kópavogur'
    return None


def event_description(soup):
    parts = []
    for element in soup.select('.article.event .cast, .article.event .content-cols'):
        text = clean_text(element)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_event(soup, url):
    title = clean_text(soup.select_one('.evtimg h1') or soup.select_one('h1'))
    description = event_description(soup)
    records = []

    for row in soup.select('.article.event table.meta tr'):
        date_cell = row.select_one('td.date')
        venue_cell = row.select_one('td.venue')
        if not date_cell or not venue_cell:
            continue

        event_date = parse_date(clean_text(date_cell))
        venue = clean_text(venue_cell)
        city = resolve_city(venue)
        time_match = re.search(r'\b([01]\d|2[0-3]):([0-5]\d)\b', clean_text(date_cell))
        if not title or not event_date or not venue or not city:
            continue

        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_match.group(0) if time_match else None,
            'venue': venue,
            'city': city,
            'country_code': 'IS',
            'description': description,
        })

    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = discover_event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_event(future.result(), url))
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
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class EnSinfoniaIsCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='en_sinfonia_is',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='IS',
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
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    EnSinfoniaIsCrawler().run()


if __name__ == '__main__':
    main()
