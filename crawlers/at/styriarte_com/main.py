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


SOURCE_URL = 'https://styriarte.com/'
PROGRAM_URL = urljoin(SOURCE_URL, 'programm')
API_URL = urljoin(SOURCE_URL, 'api/filtered-program')
SOURCE = 'Styriarte'
WEEKDAYS = {'Mo': 0, 'Di': 1, 'Mi': 2, 'Do': 3, 'Fr': 4, 'Sa': 5, 'So': 6}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_json(session, page):
    response = session.get(
        API_URL,
        params={
            'page': page,
            'filterTicketsAvailable': 'false',
            'startDate': '2000-01-01',
            'endDate': '',
            'currentLocale': 'default',
        },
        timeout=45,
    )
    response.raise_for_status()
    return response.json()


def parse_listing(html):
    soup = BeautifulSoup(html or '', 'html.parser')
    events = []
    for item in soup.select('li.js-event-teaser-item'):
        production_link = item.select_one('a[href^="/produktionen/"]')
        title = clean_text(item.get('data-title'))
        date_node = item.select_one('.date')
        date_node = date_node or item.select_one('.headline')
        schedule_node = item.select_one('.text-program')
        venue_node = item.select_one('div.text-program.hidden')
        venue = clean_text(venue_node)
        if not all((production_link, title, date_node, schedule_node, venue)):
            continue
        day_month = re.search(r'(\d{1,2})\.(\d{1,2})\.', clean_text(date_node))
        time_match = re.search(r'\b(\d{1,2}):(\d{2})\b', clean_text(schedule_node))
        weekday_match = re.search(r'\b(Mo|Di|Mi|Do|Fr|Sa|So)\b', clean_text(schedule_node))
        if not day_month:
            continue
        events.append({
            'title': title,
            'day': int(day_month.group(1)),
            'month': int(day_month.group(2)),
            'time_from': (
                f'{int(time_match.group(1)):02d}:{time_match.group(2)}'
                if time_match else None
            ),
            'weekday': WEEKDAYS.get(weekday_match.group(1)) if weekday_match else None,
            'url': urljoin(SOURCE_URL, production_link['href']),
            'venue': venue,
            'teaser': clean_text(item.select_one('.text-copy.mb-16')) or None,
        })
    return events


def fetch_catalog(session):
    first = get_json(session, 1)
    pages = {1: first}
    last_page = int(first.get('last_page') or 1)
    # This endpoint is rate limited. A small pool is considerably more reliable
    # than firing all archive pages at once.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(get_json, session, page): page for page in range(2, last_page + 1)}
        for future in as_completed(futures):
            page = futures[future]
            try:
                pages[page] = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to fetch Styriarte programme page',
                    event='crawler_page_failed',
                    level='warning',
                    url=API_URL,
                    page=page,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    missing_pages = sorted(set(range(1, last_page + 1)) - pages.keys())
    if missing_pages:
        raise requests.RequestException(
            f'Styriarte programme archive incomplete; missing {len(missing_pages)} pages'
        )
    return [event for page in sorted(pages) for event in parse_listing(pages[page].get('results'))]


def production_detail(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    title_text = clean_text(soup.title)
    year_match = re.search(r'\b(20\d{2})\b', title_text)
    if not year_match:
        published = soup.select_one('meta[property="article:published_time"]')
        year_match = re.search(r'\b(20\d{2})\b', published.get('content', '')) if published else None

    parts = []
    intro = soup.select_one('.visual-intro-block .copy-wrapper')
    if intro:
        parts.append(clean_text(intro))
    for block in soup.select('.section-block .wysiwyg'):
        text = clean_text(block)
        if text and text not in parts:
            parts.append(text)
    locations = {
        clean_text(link): urljoin(SOURCE_URL, link['href'])
        for link in soup.select('.upcoming-shows-program-detail-block a[href^="/locations/"]')
        if clean_text(link)
    }
    return (
        int(year_match.group(1)) if year_match else None,
        '\n\n'.join(parts) or None,
        locations,
    )


def location_city(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    block = soup.select_one('.location-block')
    if block:
        address = clean_text(block.select_one('.link'))
        match = re.search(r'\b\d{4}\s+([^\n,]+)', address)
        if match:
            return match.group(1).strip()
    description = soup.select_one('meta[name="description"]')
    text = description.get('content', '') if description else ''
    match = re.search(r'\bin\s+([A-ZÄÖÜ][\wÄÖÜäöüß .-]+?)(?:[.,]|$)', text)
    return match.group(1).strip() if match else None


def fetch_map(session, urls, function, label):
    results = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(function, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except (requests.RequestException, ValueError) as error:
                log_message(
                    f'Failed to fetch Styriarte {label}',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return results


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
        respect_retry_after_header=True,
    )
    session.mount('https://', HTTPAdapter(max_retries=retry, pool_connections=6, pool_maxsize=6))
    events = fetch_catalog(session)
    details = fetch_map(session, {event['url'] for event in events}, production_detail, 'production')
    location_urls = {
        detail[2].get(event['venue'])
        for event in events
        for detail in [details.get(event['url'])]
        if detail and detail[2].get(event['venue'])
    }
    cities = fetch_map(session, location_urls, location_city, 'location')

    records = []
    for event in events:
        detail = details.get(event['url'])
        if not detail:
            continue
        year, description, locations = detail
        city = cities.get(locations.get(event['venue']))
        if not year or not city:
            continue
        candidates = []
        for candidate_year in (year - 1, year, year + 1):
            try:
                candidate = date(candidate_year, event['month'], event['day'])
            except ValueError:
                continue
            if event['weekday'] is None or candidate.weekday() == event['weekday']:
                candidates.append(candidate)
        if not candidates:
            continue
        event_date = min(candidates, key=lambda candidate: abs(candidate.year - year)).isoformat()
        records.append({
            'title': event['title'],
            'date': event_date,
            'url': event['url'],
            'time_from': event['time_from'],
            'venue': event['venue'],
            'city': city,
            'country_code': 'AT',
            'description': description or event['teaser'],
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return sorted(records, key=lambda record: (
        record['date'], record['time_from'] or '', record['title'], record['venue']
    ))


class StyriarteComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='styriarte_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
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
    StyriarteComCrawler().run()


if __name__ == '__main__':
    main()
