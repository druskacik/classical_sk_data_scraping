import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://marlowetheatre.com/'
SOURCE = 'The Marlowe'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on/')
LOAD_MORE_URL = urljoin(
    SOURCE_URL, 'wp-content/themes/Marlowe/page-templates/load-more.php'
)
CITY = 'Canterbury'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

VENUES = {
    'main house': 'The Marlowe Theatre',
    'theatre': 'The Marlowe Theatre',
    'studio': 'The Marlowe Studio',
    'kit': 'The Marlowe Kit',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def listing_urls(session):
    # The normal page contains the first batch. The endpoint observed in the
    # browser supplies the remainder of the current catalogue in one response.
    soups = [
        get_soup(session, LISTING_URL),
        get_soup(session, LOAD_MORE_URL, params={'page': 2}),
    ]
    urls = set()
    for soup in soups:
        for item in soup.select('.tease-shows'):
            genres = set((item.get('data-genre') or '').split())
            if not genres.intersection({'classical-music', 'opera'}):
                continue
            link = item.select_one('.post-title a[href]')
            if link:
                urls.add(urljoin(SOURCE_URL, link.get('href')))
    return urls


def parse_date_range(value):
    text = clean_text(value).lower().replace('–', '-').replace('—', '-')
    matches = re.findall(r'(\d{1,2})\s+([a-z]{3})\s+(\d{4})', text)
    if not matches:
        return []
    try:
        start = date(int(matches[0][2]), MONTHS[matches[0][1]], int(matches[0][0]))
        end = start
        if len(matches) > 1:
            end = date(int(matches[-1][2]), MONTHS[matches[-1][1]], int(matches[-1][0]))
        elif '-' in text:
            short = re.search(
                r'(\d{1,2})\s*(?:-|&dash;)\s*(?:[a-z]{3}\s+)?'
                r'(\d{1,2})\s+([a-z]{3})\s+(\d{4})',
                text,
            )
            if short:
                end = date(int(short.group(4)), MONTHS[short.group(3)], int(short.group(2)))
                start = date(end.year, end.month, int(short.group(1)))
    except (KeyError, ValueError):
        return []
    if end < start or (end - start).days > 62:
        return []
    return [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]


def parse_time(value):
    match = re.search(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', clean_text(value), re.I)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour < 1 or hour > 12 or minute > 59:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def sidebar_time(sidebar):
    for row in sidebar.select('.show-info dl'):
        if clean_text(row.find('dt')).casefold() == 'time':
            return parse_time(row.find('dd'))
    return None


def detail_records(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.select_one('h1.article-title'))
    body = soup.select_one('article.post-type-shows .article-body')
    sidebar = soup.select_one('#sidebar')
    if not title or not sidebar:
        return []

    venue_text = clean_text(sidebar.select_one(':scope > .venue')).casefold()
    venue = VENUES.get(venue_text)
    dates = parse_date_range(sidebar.select_one(':scope > .date-range'))
    if not venue or not dates:
        return []

    if body:
        for element in body.select('.supporters, script, style'):
            element.decompose()
    description = clean_text(body) or None
    time_from = sidebar_time(sidebar)
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': CITY,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date in dates
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(detail_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Marlowe event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class MarloweTheatreComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='marlowetheatre_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
    MarloweTheatreComCrawler().run()


if __name__ == '__main__':
    main()
