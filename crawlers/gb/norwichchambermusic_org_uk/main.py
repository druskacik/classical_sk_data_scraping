import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.norwichchambermusic.org.uk/'
SOURCE = 'Norwich Chamber Music'
SITEMAP_URL = f'{SOURCE_URL}sitemap.xml'
CITY = 'Norwich'
DEFAULT_VENUE = 'John Innes Centre Conference Centre'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

DATE_TIME_RE = re.compile(
    r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+'
    r'(\d{1,2}\s+[A-Za-z]+\s+20\d{2})'
    r'(?:\s*,\s*(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm))?\b',
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


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount(
        'https://',
        HTTPAdapter(max_retries=Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=('GET',),
        )),
    )
    return session


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def concert_urls(session):
    index = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    sitemap_urls = [clean_text(node) for node in index.select('sitemap > loc')]
    urls = []
    for sitemap_url in sitemap_urls:
        sitemap = BeautifulSoup(get_response(session, sitemap_url).content, 'xml')
        for node in sitemap.select('url > loc'):
            url = clean_text(node)
            path = urlparse(url).path
            if re.fullmatch(r'/concerts/[^/]+/', path) and not path.startswith('/concerts/venues/'):
                if path not in ('/concerts/tickets/', '/concerts/joe-stirling-memorial-concert/'):
                    urls.append(url)
    return list(dict.fromkeys(urls))


def parse_date_time(text):
    match = DATE_TIME_RE.search(text)
    if not match:
        return None, None
    try:
        event_date = datetime.strptime(match.group(1), '%d %B %Y').date().isoformat()
    except ValueError:
        return None, None
    if not match.group(2):
        return event_date, None
    hour = int(match.group(2))
    minute = int(match.group(3) or 0)
    if hour < 1 or hour > 12 or minute > 59:
        return event_date, None
    if match.group(4).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(4).lower() == 'am' and hour == 12:
        hour = 0
    return event_date, f'{hour:02d}:{minute:02d}'


def parse_concert(url, content):
    soup = BeautifulSoup(content, 'html.parser')
    title = re.sub(r'^Archive:\s*', '', clean_text(soup.select_one('h1')), flags=re.IGNORECASE)
    main = soup.select_one('.concert-page__main')
    aside = soup.select_one('.concert-page__aside')
    if not title or not main or not aside:
        return None

    event_date, time_from = parse_date_time(clean_text(main.select_one('h2')))
    venue = clean_text(aside.select_one('h2 + p strong, h2 + p')) or DEFAULT_VENUE
    if not event_date or not venue:
        return None

    description_parts = []
    for node in main.select('h3, p, ul, ol'):
        text = clean_text(node)
        if text and text not in description_parts:
            description_parts.append(text)

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': CITY,
        'country_code': 'GB',
        'description': '\n\n'.join(description_parts) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = make_session()
    urls = concert_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_concert(url, future.result().content)
                if record:
                    records.append(record)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Norwich Chamber Music concert',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class NorwichChamberMusicOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='norwichchambermusic_org_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
    NorwichChamberMusicOrgUkCrawler().run()


if __name__ == '__main__':
    main()
