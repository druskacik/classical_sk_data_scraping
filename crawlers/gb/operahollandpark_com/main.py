import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://operahollandpark.com/'
SOURCE = 'Opera Holland Park'
SITEMAP_URL = f'{SOURCE_URL}productions-sitemap.xml'
CITY = 'London'
DEFAULT_VENUE = 'Opera Holland Park Theatre'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# Productions normally take place in the company's venue. These named
# exceptions also occur in its calendar and are all unambiguously in London.
VENUE_MARKERS = {
    "drapers' hall": "Drapers' Hall",
    'drapers hall': "Drapers' Hall",
    'st mary abbots church': 'St Mary Abbots Church',
    'holy trinity brompton': 'Holy Trinity Brompton',
    'opera holland park theatre': DEFAULT_VENUE,
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def production_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    urls = []
    for node in soup.select('url > loc'):
        url = clean_text(node)
        if re.fullmatch(r'https://operahollandpark\.com/productions/[^/]+/', url):
            urls.append(url)
    return list(dict.fromkeys(urls))


def parse_time(value):
    value = clean_text(value).lower().replace('.', ':').replace(' ', '')
    if value in ('noon', '12noon'):
        return '12:00'
    match = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?(am|pm)', value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour < 1 or hour > 12 or minute > 59:
        return None
    if match.group(3) == 'pm' and hour != 12:
        hour += 12
    elif match.group(3) == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def page_description(soup):
    parts = []
    composer = clean_text(soup.select_one('h2.composer'))
    if composer:
        parts.append(f'Composer: {composer}')
    for selector in ('.article-content .intro', '.article-content .article-body'):
        for element in soup.select(selector):
            text = clean_text(element)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def resolve_venue(description):
    normalized = (description or '').casefold().replace('\u2019', "'")
    for marker, venue in VENUE_MARKERS.items():
        if marker in normalized:
            return venue
    return DEFAULT_VENUE


def parse_production(url, content):
    soup = BeautifulSoup(content, 'html.parser')
    title = clean_text(soup.select_one('h1'))
    description = page_description(soup)
    venue = resolve_venue(description)
    records = []

    for month in soup.select('.instances .month'):
        month_name = clean_text(month.select_one('.month-name'))
        try:
            month_date = datetime.strptime(month_name, '%B %Y')
        except ValueError:
            continue
        for instance in month.select('.month-instances > li'):
            day_text = clean_text(instance.select_one('.monthday'))
            time_from = parse_time(clean_text(instance.select_one('.time')))
            try:
                event_date = date(month_date.year, month_date.month, int(day_text)).isoformat()
            except (TypeError, ValueError):
                continue
            if not title or not venue:
                continue
            records.append({
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
            })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount(
        'https://',
        HTTPAdapter(
            max_retries=Retry(
                total=2,
                backoff_factor=0.5,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=('GET',),
            )
        ),
    )
    urls = production_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_production(url, future.result().content))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Opera Holland Park production',
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


class OperaHollandParkComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='operahollandpark_com',
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
    OperaHollandParkComCrawler().run()


if __name__ == '__main__':
    main()
