import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.ballet.org.uk/'
SOURCE = 'English National Ballet'
PRODUCTIONS_API = f'{SOURCE_URL}wp-json/wp/v2/production'

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


def get_response(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return response


def production_pages(session):
    page = 1
    productions = []
    while True:
        response = get_response(
            session,
            PRODUCTIONS_API,
            {'per_page': 100, 'page': page, '_fields': 'link,title'},
        )
        productions.extend(response.json())
        total_pages = int(response.headers.get('X-WP-TotalPages', '1'))
        if page >= total_pages:
            return productions
        page += 1


def venue_cities(soup):
    result = {}
    for block in soup.select('.introduction-details__location_date .intro-ld__block'):
        city_node = block.select_one('.intro-ld__location')
        venue_node = block.select_one('.introduction-details__location span')
        city = clean_text(city_node)
        venue = clean_text(venue_node)
        if city and venue:
            result[venue.casefold()] = (venue, city)
    return result


def cast_data(soup):
    node = soup.select_one('script#cast-data')
    if not node:
        return []
    try:
        value = json.loads(node.string or node.get_text())
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def description_text(soup, casts):
    parts = []
    for selector in (
        '.page-introduction__intro',
        '.page-introduction__text',
        '.benifits__text',
        '.article-content',
    ):
        for node in soup.select(selector):
            text = clean_text(node)
            if text and text not in parts:
                parts.append(text)

    creative_lines = []
    for cast in casts:
        team = ((cast.get('production_team_and_dates') or {}).get('creative_team') or [])
        for person in team:
            name = clean_text(person.get('name'))
            role = clean_text(person.get('role'))
            line = f'{role}: {name}' if role and name else name or role
            if line and line not in creative_lines:
                creative_lines.append(line)
    if creative_lines:
        parts.append('Creative team\n' + '\n'.join(creative_lines))
    return '\n\n'.join(parts) or None


def parse_datetime(value):
    try:
        parsed = datetime.strptime(str(value).strip(), '%d/%m/%Y %I:%M %p')
    except (TypeError, ValueError):
        return None, None
    return parsed.date().isoformat(), parsed.strftime('%H:%M')


def production_records(session, production):
    url = production.get('link') or ''
    title = clean_text((production.get('title') or {}).get('rendered'))
    if not url or not title:
        return []

    soup = BeautifulSoup(get_response(session, url).content, 'html.parser')
    locations = venue_cities(soup)
    casts = cast_data(soup)
    description = description_text(soup, casts)
    records = []
    for cast in casts:
        location = clean_text(cast.get('location'))
        venue_city = locations.get(location.casefold())
        if not venue_city:
            continue
        venue, city = venue_city
        dates = ((cast.get('production_team_and_dates') or {}).get('perfomance_dates') or [])
        for performance in dates:
            event_date, time_from = parse_datetime(performance.get('date'))
            if not event_date:
                continue
            records.append({
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': 'GB',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    productions = production_pages(session)
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(production_records, session, production): production
            for production in productions
        }
        for future in as_completed(futures):
            production = futures[future]
            try:
                records.extend(future.result())
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape English National Ballet production',
                    event='crawler_item_failed',
                    level='warning',
                    url=production.get('link'),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class BalletOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='ballet_org_uk',
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
    BalletOrgUkCrawler().run()


if __name__ == '__main__':
    main()
