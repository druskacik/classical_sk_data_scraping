import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.eno.org/'
SOURCE = 'English National Opera'
SITEMAP_URL = f'{SOURCE_URL}event-sitemap.xml'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def build_session():
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
    return session


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def event_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    urls = []
    for node in soup.select('url > loc'):
        url = clean_text(node)
        if re.fullmatch(r'https://www\.eno\.org/events/[^/]+/', url):
            urls.append(url)
    return list(dict.fromkeys(urls))


def page_description(soup):
    parts = []
    for selector in ('.main-content__intro', '.main-content__text'):
        text = clean_text(soup.select_one(selector))
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def booking_data(soup):
    for script in soup.select('script:not([src])'):
        text = script.get_text(strip=True)
        if not text.startswith('[') or 'raw_date' not in text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return value
    return []


def archived_dates(soup):
    """Recover exact dates when ENO has removed past Spektrix bookings."""
    node = soup.select_one('.event-title__venue-time[datetime]')
    if not node:
        return []
    value = node.get('datetime', '')
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2})(?:/(\d{4}-\d{2}-\d{2}))?', value)
    if not match or (match.group(2) and match.group(1) != match.group(2)):
        return []
    try:
        return [(datetime.strptime(match.group(1), '%Y-%m-%d'), None)]
    except ValueError:
        return []


def resolve_place(venue):
    normalized = venue.casefold()
    if venue.casefold() in {'venue tbc', 'tbc', 'to be confirmed'}:
        return None
    if 'bridgewater hall' in normalized or 'manchester' in normalized:
        return 'Manchester'
    if 'grange park opera' in normalized or 'surrey' in normalized:
        return 'West Horsley'
    if 'london' in normalized or 'eno costume workshop' in normalized:
        return 'London'
    return None


def parse_event(url, content):
    soup = BeautifulSoup(content, 'html.parser')
    title = clean_text(soup.select_one('.page-header__heading'))
    venue = clean_text(soup.select_one('.event-title__venue-location'))
    city = resolve_place(venue) if venue else None
    if not title or not venue or not city:
        return []

    performances = []
    for booking in booking_data(soup):
        raw_date = clean_text(booking.get('raw_date'))
        try:
            start = datetime.strptime(raw_date, '%Y-%m-%d %H:%M:%S')
        except (TypeError, ValueError):
            continue
        performances.append((start, start.strftime('%H:%M')))
    if not performances:
        performances = archived_dates(soup)

    description = page_description(soup)
    return [
        {
            'title': title,
            'date': start.date().isoformat(),
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'description': description,
        }
        for start, time_from in performances
    ]


def get_concerts():
    session = build_session()
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_event(url, future.result().content))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape ENO event',
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


class EnoOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='eno_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city', 'description',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
    )

    def scrape(self):
        return get_concerts()

    def transform(self, df):
        # Keep unavailable archived performance times as JSON/DB nulls.
        return df.astype(object).where(df.notna(), None)


def main():
    EnoOrgCrawler().run()


if __name__ == '__main__':
    main()
