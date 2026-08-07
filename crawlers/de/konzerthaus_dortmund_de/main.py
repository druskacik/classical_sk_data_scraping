import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.konzerthaus-dortmund.de/de'
PROGRAM_URL = f'{SOURCE_URL}/programm/'
SOURCE = 'Konzerthaus Dortmund'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_html(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.text


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    session.mount('https://', HTTPAdapter(max_retries=retries, pool_maxsize=12))
    return session


def listing_urls(session):
    soup = BeautifulSoup(get_html(session, PROGRAM_URL), 'html.parser')
    urls = []
    seen = set()
    selector = 'a[aria-label="Zur Detailseite dieser Veranstaltung"]'
    for link in soup.select(selector):
        url = urljoin(PROGRAM_URL, link.get('href', ''))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def music_event_data(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get('@type') == 'MusicEvent':
                return item
    return None


def location_fields(event):
    location = event.get('location') or {}
    if not isinstance(location, dict):
        return '', '', ''
    address = location.get('address') or {}
    if not isinstance(address, dict):
        address = {}
    country = address.get('addressCountry') or ''
    if isinstance(country, dict):
        country = country.get('name') or ''
    return (
        clean_text(location.get('name')),
        clean_text(address.get('addressLocality')),
        clean_text(country).upper(),
    )


def detail_description(soup, event):
    parts = []
    narrative = soup.select_one('#va-program .col-12.col-md-9')
    narrative = clean_text(narrative) if narrative else ''
    if narrative:
        parts.append(narrative)

    works = event.get('workPerformed') or []
    if isinstance(works, dict):
        works = [works]
    work_names = [
        clean_text(work.get('name'))
        for work in works
        if isinstance(work, dict) and clean_text(work.get('name'))
    ]
    if work_names:
        parts.append('Programm\n' + '\n'.join(work_names))

    if not parts:
        fallback = clean_text(event.get('description'))
        if fallback:
            parts.append(fallback)
    return '\n\n'.join(parts) or None


def make_record(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    event = music_event_data(soup)
    if not event:
        return None

    title = clean_text(event.get('name'))
    # JSON-LD names append the date and venue to the actual displayed title.
    heading = soup.select_one('h1')
    if heading:
        title = clean_text(heading)

    start = str(event.get('startDate') or '')
    match = re.match(r'^(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2}))?', start)
    venue, city, country_code = location_fields(event)
    canonical_url = urljoin(url, event.get('url') or url)
    if not title or not match or not canonical_url or not venue or not city:
        return None
    try:
        event_date = date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None

    # The source is a Dortmund venue calendar. Keep only German records; an
    # explicitly different event location must not inherit the home geography.
    if country_code not in ('DE', 'DEU', 'GER', 'GERMANY', 'DEUTSCHLAND'):
        return None

    time_from = None
    if match.group(2) and match.group(3):
        time_from = f'{match.group(2)}:{match.group(3)}'
    return {
        'title': title,
        'date': event_date,
        'url': canonical_url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'DE',
        'description': detail_description(soup, event),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = make_session()
    urls = listing_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(get_html, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = make_record(url, future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class KonzerthausDortmundDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='konzerthaus_dortmund_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
        upload_target='potential',
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
    KonzerthausDortmundDeCrawler().run()


if __name__ == '__main__':
    main()
