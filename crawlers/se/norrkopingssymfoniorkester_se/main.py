import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.norrkopingssymfoniorkester.se/'
SOURCE = 'Norrköpings Symfoniorkester'
GRAPHQL_URL = urljoin(SOURCE_URL, 'api/graphql')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        'Chrome/125.0 Safari/537.36'
    ),
    'Content-Type': 'application/json',
    'X-CMS-Module-Version': 'scenkonst',
    'X-CMS-Current-Path': '/',
    'X-CMS-Project': '@scenkonst/son-frontend',
}
EVENT_QUERY = '''
query performanceEvents($id: ID) {
  scenkonstOtPerformanceEvents(performanceId: $id) {
    id
    startDate
    endDate
    cancelled
    locationStage
    performance { id title pageUrl }
  }
}
'''

# The API normally includes the municipality after a comma. These are the
# unqualified stages currently used by the orchestra's own calendar.
VENUE_CITIES = {
    'de geerhallen onumrerad': 'Norrköping',
    'equmeniakyrkan': 'Linköping',
    'flygeln': 'Norrköping',
    'vasaparken': 'Norrköping',
    'åtvids stora kyrka': 'Åtvidaberg',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def split_location(value):
    location = clean_text(value)
    if not location:
        return '', ''
    if ',' in location:
        venue, city = (part.strip() for part in location.rsplit(',', 1))
        return venue, city
    return location, VENUE_CITIES.get(location.casefold(), '')


def fetch_description(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    main = soup.select_one('main')
    if not main:
        return None

    for heading in main.find_all(['h2', 'h3', 'h4']):
        if clean_text(heading).casefold() != 'om konserten':
            continue
        article = heading.find_parent('article')
        if not article:
            continue
        body = article.find(attrs={'role': 'region'}) or article
        description = clean_text(body)
        if description.casefold().startswith('om konserten'):
            description = description[len('om konserten'):].lstrip()
        return description or None
    return None


def fetch_events(session):
    response = session.post(
        GRAPHQL_URL,
        json={
            'operationName': 'performanceEvents',
            'variables': {},
            'query': EVENT_QUERY,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('errors'):
        raise RuntimeError(f"GraphQL returned {len(payload['errors'])} error(s)")
    return payload.get('data', {}).get('scenkonstOtPerformanceEvents') or []


class NorrkopingssymfoniorkesterSeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='norrkopingssymfoniorkester_se',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='SE',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        events = fetch_events(session)

        urls = {
            urljoin(SOURCE_URL, event.get('performance', {}).get('pageUrl', ''))
            for event in events
            if event.get('performance', {}).get('pageUrl')
        }
        descriptions = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_description, session, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    descriptions[url] = future.result()
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )

        records = []
        for event in events:
            performance = event.get('performance') or {}
            title = clean_text(performance.get('title'))
            page_url = performance.get('pageUrl')
            url = urljoin(SOURCE_URL, page_url) if page_url else ''
            venue, city = split_location(event.get('locationStage'))
            try:
                start = datetime.fromisoformat(event.get('startDate', ''))
            except (TypeError, ValueError):
                continue
            if event.get('cancelled') or not all((title, url, venue, city)):
                continue
            records.append({
                'title': title,
                'date': start.date().isoformat(),
                'url': url,
                'time_from': start.strftime('%H:%M'),
                'venue': venue,
                'city': city,
                'country_code': 'SE',
                'description': descriptions.get(url),
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })

        log_message(
            'Concert schedule scraped',
            event='crawler_schedule_scraped',
            url=GRAPHQL_URL,
            record_count=len(records),
        )
        return sorted(records, key=lambda item: (
            item['date'], item['time_from'], item['title'], item['venue']
        ))


def main():
    NorrkopingssymfoniorkesterSeCrawler().run()


if __name__ == '__main__':
    main()
