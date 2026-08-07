import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.operadeparis.fr/'
SOURCE = 'Opéra national de Paris'
AGENDA_URL = urljoin(SOURCE_URL, 'ajax/agenda/details/dates-{start}+{end}')
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap-fr.xml')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9',
}

# The agenda includes a small number of tours. Only locations for which the
# city is explicit or independently unambiguous are accepted.
VENUE_CITIES = {
    'palais garnier': 'Paris',
    'opéra bastille': 'Paris',
    'opera bastille': 'Paris',
    'amphithéâtre olivier messiaen': 'Paris',
    'amphitheatre olivier messiaen': 'Paris',
    'studio bastille': 'Paris',
    'rotonde du glacier': 'Paris',
    'mc93 bobigny': 'Bobigny',
    'mc93 - bobigny': 'Bobigny',
    'abbatiale saint-robert': 'La Chaise-Dieu',
    'les 2 scènes besançon': 'Besançon',
    'théâtre impérial de compiègne': 'Compiègne',
    "maison de la culture d'amiens": 'Amiens',
    'mc2 - grenoble': 'Grenoble',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response


def resolve_city(venue):
    normalized = clean_text(venue).casefold()
    for marker, city in VENUE_CITIES.items():
        if marker in normalized:
            return city
    return None


def valid_date(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def agenda_bounds():
    # The server retains the published season even when the lower bound is in
    # the past. A two-year upper bound also covers a season announced early.
    start = datetime(2000, 1, 1, tzinfo=timezone.utc)
    end = datetime.combine(
        date.today() + timedelta(days=730), datetime.min.time(), tzinfo=timezone.utc
    )
    return int(start.timestamp()), int(end.timestamp())


def agenda_page(session, url, page):
    return get_response(session, url, {'page': page}).json()


def agenda_events(session):
    start, end = agenda_bounds()
    url = AGENDA_URL.format(start=start, end=end)
    first = agenda_page(session, url, 1)
    events = list(first.get('data') or [])
    pagination = (first.get('meta') or {}).get('pagination') or {}
    total_pages = int(pagination.get('total_page') or 1)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(agenda_page, session, url, page): page
            for page in range(2, total_pages + 1)
        }
        for future in as_completed(futures):
            page = futures[future]
            try:
                events.extend(future.result().get('data') or [])
            except (requests.RequestException, ValueError, TypeError) as error:
                log_message(
                    'Failed to scrape Paris Opera agenda page',
                    event='crawler_item_failed',
                    level='warning',
                    url=f'{url}?page={page}',
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return events


def schema_event(soup):
    for script in soup.find_all('script'):
        value = (script.string or '').strip()
        if not value.startswith('{') or 'schema.org' not in value:
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        if payload.get('@type') in ('MusicEvent', 'TheaterEvent', 'DanceEvent'):
            return payload
    return {}


def page_description(soup, schema):
    parts = [clean_text(schema.get('description'))]
    for selector in (
        '.component-season-presentation',
        '.component-casting',
        '.component-season-description',
    ):
        for element in soup.select(selector):
            text = clean_text(element)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(part for part in parts if part) or None


def detail_data(session, url):
    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
    schema = schema_event(soup)
    return schema, page_description(soup, schema)


def detail_map(session, urls):
    details = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(detail_data, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                details[url] = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Paris Opera event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return details


def current_record(event, descriptions):
    title = clean_text(event.get('title'))
    venue = clean_text(event.get('venue'))
    city = resolve_city(venue)
    url = event.get('full_url') or ''
    start = str(event.get('next_performance_date') or '')
    event_date = valid_date(start)
    time_match = re.search(r'\b(\d{2}):(\d{2})', start)
    if not title or not venue or not city or not url or not event_date:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': f'{time_match.group(1)}:{time_match.group(2)}' if time_match else None,
        'venue': venue,
        'city': city,
        'country_code': 'FR',
        'description': descriptions.get(url),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def archived_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    urls = [clean_text(node) for node in soup.select('url > loc')]
    return [url for url in urls if re.search(r'/saison-\d{2}-\d{2}/', url)]


def archived_record(url, schema, description):
    title = clean_text(schema.get('name'))
    event_date = valid_date(schema.get('startDate'))
    location = schema.get('location') or {}
    venue = clean_text(location.get('name')) if isinstance(location, dict) else ''
    city = resolve_city(venue)
    if not title or not event_date or not venue or not city:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': None,
        'venue': venue,
        'city': city,
        'country_code': 'FR',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    events = agenda_events(session)
    current_urls = {event.get('full_url') for event in events if event.get('full_url')}
    archive_urls = set(archived_urls(session)) - current_urls
    details = detail_map(session, current_urls | archive_urls)

    descriptions = {url: value[1] for url, value in details.items()}
    records = [current_record(event, descriptions) for event in events]
    for url in archive_urls:
        schema, description = details.get(url, ({}, None))
        records.append(archived_record(url, schema, description))

    records = [record for record in records if record]
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class OperaDeParisFrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='operadeparis_fr',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
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
    OperaDeParisFrCrawler().run()


if __name__ == '__main__':
    main()
