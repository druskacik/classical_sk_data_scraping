import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.melbournerecital.com.au/'
SOURCE = 'Melbourne Recital Centre'
CITY = 'Melbourne'
API_URL = 'https://api.melbournerecital.com.au/api'
LISTING_URL = f'{API_URL}/WhatsOn/Productions'
DETAIL_URL = f'{API_URL}/WhatsOn/Production'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/html;q=0.9',
    'Accept-Language': 'en-AU,en;q=0.9',
    'Origin': SOURCE_URL.rstrip('/'),
    'Referer': SOURCE_URL,
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, url, *, params=None, payload=None):
    if payload is None:
        response = session.get(url, params=params, timeout=60)
    else:
        response = session.post(url, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    if data.get('isError'):
        raise ValueError(data.get('detail') or data.get('title') or 'API returned an error')
    return data.get('result') or {}


def listing_productions(session):
    # The public calendar accepts an arbitrary start date. The site currently
    # retains its 2026 archive, so use an old boundary and follow every page.
    page = 1
    productions = {}
    while True:
        result = get_json(
            session,
            LISTING_URL,
            payload={'pageNumber': page, 'dates': [], 'startDate': '2000-01-01'},
        )
        for day in result.get('dates') or []:
            for production in day.get('productions') or []:
                production_id = production.get('id')
                if production_id:
                    saved = productions.setdefault(production_id, dict(production, _dates=[]))
                    for value in production.get('times') or []:
                        if value not in saved['_dates']:
                            saved['_dates'].append(value)

        total_pages = int(result.get('totalPages') or page)
        if page >= total_pages or not result.get('hasMore'):
            break
        page += 1

    if not productions:
        raise ValueError('The public calendar API returned no productions')
    return list(productions.values())


def event_description(html):
    soup = BeautifulSoup(html, 'html.parser')
    main = soup.find('main')
    if main is None:
        return None

    blocks = []
    for element in main.select('.wysiwyg-content'):
        text = clean_text(element)
        if not text or text == '[object Object]':
            continue
        lowered = text.casefold()
        is_programme = re.search(r'(^|\n)(?:program|programme)(?:\n|$)', lowered)
        is_synopsis = not blocks and not lowered.startswith(
            ('duration:', 'tickets\n', 'ticket prices\n', 'want to book')
        )
        if (is_synopsis or is_programme) and text not in blocks:
            blocks.append(text)
    return '\n\n'.join(blocks) or None


def parse_datetime(value):
    try:
        parsed = datetime.fromisoformat(value)
        # Round-tripping through date validates dates such as 2026-02-30 even
        # if a future API representation is no longer accepted by datetime.
        event_date = date.fromisoformat(parsed.date().isoformat()).isoformat()
    except (TypeError, ValueError):
        return None
    return event_date, parsed.strftime('%H:%M')


def parse_production(session, production):
    production_id = production.get('id')
    route = production.get('route') or ''
    url = urljoin(SOURCE_URL, route)
    title = clean_text(production.get('name'))
    venue = clean_text(production.get('venue'))
    if not production_id or not route or not title or not venue:
        return []

    try:
        detail = get_json(session, DETAIL_URL, params={'Id': production_id})
    except (requests.RequestException, ValueError):
        # Past productions can remain in the calendar after their Tessitura
        # detail record is retired. The listing still supplies exact sessions.
        detail = {}
    title = clean_text(detail.get('name')) or title
    venue = clean_text(detail.get('venue')) or venue

    description = None
    try:
        response = session.get(url, timeout=60)
        response.raise_for_status()
        description = event_description(response.text)
    except requests.RequestException:
        # Description is optional, and archived catalogue rows remain valid
        # even after their editorial page is unpublished.
        pass

    records = []
    seen_sessions = set()
    datetimes = []
    for value in (detail.get('dates') or []) + (production.get('_dates') or []):
        if value not in datetimes:
            datetimes.append(value)
    for value in datetimes:
        parsed = parse_datetime(value)
        if not parsed:
            continue
        event_date, time_from = parsed
        session_key = (event_date, time_from)
        if session_key in seen_sessions:
            continue
        seen_sessions.add(session_key)
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': CITY,
            'country_code': 'AU',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


class MelbourneRecitalComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='melbournerecital_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        productions = listing_productions(session)
        records = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(parse_production, session, production): production
                for production in productions
            }
            for future in as_completed(futures):
                production = futures[future]
                try:
                    records.extend(future.result())
                except (requests.RequestException, ValueError) as error:
                    log_message(
                        'Failed to scrape concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=urljoin(SOURCE_URL, production.get('route') or ''),
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['venue']
            ),
        )


def main():
    MelbourneRecitalComAuCrawler().run()


if __name__ == '__main__':
    main()
