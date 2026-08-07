import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.rossinioperafestival.it/'
ARCHIVE_URL = f'{SOURCE_URL}archivio/'
SOURCE = 'Rossini Opera Festival'
CITY = 'Pesaro'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'it-IT,it;q=0.9,en;q=0.7',
}

MONTHS = {
    'gennaio': 1,
    'febbraio': 2,
    'marzo': 3,
    'aprile': 4,
    'maggio': 5,
    'giugno': 6,
    'luglio': 7,
    'agosto': 8,
    'settembre': 9,
    'ottobre': 10,
    'novembre': 11,
    'dicembre': 12,
}

DATE_GROUP_RE = re.compile(
    r'(?P<days>\d{1,2}(?:\s*(?:[,/]|​e​|e)\s*\d{1,2})*)\s*'
    r'(?P<month>' + '|'.join(MONTHS) + r')'
    r'(?:\s*,?\s*ore\s*(?P<hour>[0-2]?\d)[.:](?P<minute>[0-5]\d))?',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_page(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def season_urls(session):
    soup = BeautifulSoup(get_page(session, ARCHIVE_URL), 'html.parser')
    urls = {
        urljoin(ARCHIVE_URL, anchor['href'])
        for anchor in soup.select(
            'a[href*="/archivio/anno-"], a[href*="/archivio/stagione-"]'
        )
    }
    return sorted(urls)


def parse_occurrences(text, year):
    occurrences = []
    for match in DATE_GROUP_RE.finditer(clean_text(text).lower()):
        month = MONTHS[match.group('month')]
        time_from = None
        if match.group('hour') is not None:
            hour = int(match.group('hour'))
            if hour <= 23:
                time_from = f'{hour:02d}:{match.group("minute")}'
        for day_text in re.findall(r'\d{1,2}', match.group('days')):
            try:
                event_date = date(year, month, int(day_text)).isoformat()
            except ValueError:
                continue
            occurrences.append((event_date, time_from))
    return occurrences


def listing_items(session, season_url):
    soup = BeautifulSoup(get_page(session, season_url), 'html.parser')
    year_match = re.search(r'(?:anno|stagione)-(\d{4})', season_url)
    if not year_match:
        return []
    year = int(year_match.group(1))
    items = []
    for node in soup.select('.archDettagli'):
        title_link = node.select_one('h4 a[href]')
        date_node = node.select_one('span')
        venue_links = node.select('li a[href*="/luoghi/"]')
        # Some early archive cards combine several venues without saying which
        # performance used which one. Such cards cannot yield valid records.
        if not title_link or not date_node or len(venue_links) != 1:
            continue
        title = clean_text(title_link)
        url = urljoin(season_url, title_link.get('href', ''))
        venue = clean_text(venue_links[0])
        occurrences = parse_occurrences(date_node, year)
        if title and url and venue and occurrences:
            items.append((title, url, venue, occurrences))
    return items


def detail_description(session, url):
    soup = BeautifulSoup(get_page(session, url), 'html.parser')
    content = soup.select_one('#content')
    return clean_text(content) or None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = []
    for season_url in season_urls(session):
        try:
            items.extend(listing_items(session, season_url))
        except requests.RequestException as error:
            log_message(
                'Failed to fetch Rossini Opera Festival season',
                event='crawler_item_failed',
                level='warning',
                url=season_url,
                error_type=type(error).__name__,
                error_message=str(error),
            )

    descriptions = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(detail_description, session, url): url
            for _, url, _, _ in items
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                descriptions[url] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Rossini Opera Festival event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                descriptions[url] = None

    records = []
    for title, url, venue, occurrences in items:
        for event_date, time_from in occurrences:
            records.append({
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': CITY,
                'country_code': 'IT',
                'description': descriptions.get(url),
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class RossiniOperaFestivalItCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='rossinioperafestival_it',
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
        return get_concerts()


def main():
    RossiniOperaFestivalItCrawler().run()


if __name__ == '__main__':
    main()
