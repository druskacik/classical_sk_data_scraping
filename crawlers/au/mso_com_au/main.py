import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.mso.com.au/'
SOURCE = 'Melbourne Symphony Orchestra'
API_URL = urljoin(SOURCE_URL, 'api/v1/productions')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

VENUE_CITIES = {
    'Hamer Hall': 'Melbourne',
    'Iwaki Auditorium': 'Melbourne',
    'Melbourne Recital Centre': 'Melbourne',
    'Melbourne Town Hall': 'Melbourne',
    'The Plenary, Melbourne Convention And Exhibition Centre': 'Melbourne',
    'George Wood Performing Arts Centre': 'Ringwood',
    'Frankston Arts Centre': 'Frankston',
    'Somerville Recreation Centre': 'Somerville',
    'Glenroy Secondary College': 'Glenroy',
    'Wyndham Cultural Centre, Werribee': 'Werribee',
    'Berninneit, Cowes': 'Cowes',
    'The Wedge Performing Arts Centre, Sale': 'Sale',
    'Costa Hall, Geelong': 'Geelong',
    # The event deliberately withholds the venue, but is presented by this
    # Melbourne-only series and its booking page identifies it as Melbourne.
    'Secret Location': 'Melbourne',
}


def clean_text(value):
    if value is None:
        return ''
    if hasattr(value, 'get_text'):
        value = value.get_text('\n', strip=True)
    text = str(value).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_json(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def fetch_description(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    container = soup.select_one('#about-this-performance .s-cms-content')
    return clean_text(container) or None


def parse_datetime(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None, None
    return parsed.date().isoformat(), parsed.strftime('%H:%M')


def city_for_venue(venue):
    if venue in VENUE_CITIES:
        return VENUE_CITIES[venue]
    # Touring venues in the feed commonly append their locality after a comma.
    if ',' in venue:
        locality = clean_text(venue.rsplit(',', 1)[1])
        if re.fullmatch(r"[A-Za-z][A-Za-z .'-]+", locality):
            return locality
    return None


def records_for_production(production, description=None):
    title = clean_text(production.get('title'))
    detail_link = clean_text(production.get('detailLink'))
    url = urljoin(SOURCE_URL, detail_link)
    if not title or not detail_link:
        return []

    records = []
    for performance in production.get('dates') or []:
        event_date, time_from = parse_datetime(performance.get('date'))
        locations = performance.get('location') or []
        venue = clean_text(locations[0]) if locations else ''
        city = city_for_venue(venue)
        if not event_date or not venue or not city:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'AU',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


class MsoComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mso_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        productions = fetch_json(session, API_URL)
        records = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {}
            for production in productions:
                detail_link = clean_text(production.get('detailLink'))
                if detail_link:
                    detail_url = urljoin(SOURCE_URL, detail_link)
                    futures[executor.submit(fetch_description, session, detail_url)] = production

            for future in as_completed(futures):
                production = futures[future]
                detail_url = urljoin(SOURCE_URL, clean_text(production.get('detailLink')))
                try:
                    description = future.result()
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch MSO concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=detail_url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                    description = None
                records.extend(records_for_production(production, description))

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['venue']
            ),
        )


def main():
    MsoComAuCrawler().run()


if __name__ == '__main__':
    main()
