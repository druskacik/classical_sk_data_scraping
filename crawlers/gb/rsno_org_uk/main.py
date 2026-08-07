import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.rsno.org.uk/'
SITEMAP_URL = urljoin(SOURCE_URL, 'liveevent-sitemap.xml')
LISTING_URL = urljoin(SOURCE_URL, 'whats-on/')
SOURCE = 'Royal Scottish National Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# Some tour dates describe the country, rather than the city, as addressLocality.
TOUR_VENUES = {
    'kurhaus wiesbaden': ('Wiesbaden', 'DE'),
}


def clean_text(value):
    if value is None:
        return ''
    raw = str(value)
    text = (
        BeautifulSoup(raw, 'html.parser').get_text('\n', strip=True)
        if '<' in raw
        else unescape(raw)
    )
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def discover_urls(session):
    urls = set()
    response = session.get(SITEMAP_URL, timeout=45)
    response.raise_for_status()
    sitemap = BeautifulSoup(response.content, 'xml')
    for node in sitemap.select('url > loc'):
        url = clean_text(node)
        if re.match(r'^https?://(?:www\.)?rsno\.org\.uk/liveevent/', url):
            urls.add(url)

    # Include newly published items even if the sitemap has not refreshed yet.
    listing = get_soup(session, LISTING_URL)
    for link in listing.select('a[href*="/liveevent/"]'):
        urls.add(urljoin(SOURCE_URL, link.get('href')))
    return urls


def event_nodes(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        nodes = payload.get('@graph', []) if isinstance(payload, dict) else payload
        if not isinstance(nodes, list):
            nodes = [nodes]
        for node in nodes:
            event_type = node.get('@type') if isinstance(node, dict) else None
            if event_type == 'Event' or (
                isinstance(event_type, list) and 'Event' in event_type
            ):
                yield node


def resolve_location(event):
    location = event.get('location') or {}
    if not isinstance(location, dict):
        return None
    venue = clean_text(location.get('name'))
    address = location.get('address') or {}
    city = clean_text(address.get('addressLocality')) if isinstance(address, dict) else ''
    country = clean_text(address.get('addressCountry')) if isinstance(address, dict) else ''

    override = TOUR_VENUES.get(venue.casefold())
    if override:
        city, country_code = override
    else:
        country_value = country.casefold()
        country_code = 'DE' if country_value in {'de', 'deu', 'germany'} else 'GB'
    if city.casefold() in {'germany', 'deutschland'}:
        return None
    if not venue or not city:
        return None
    return venue, city, country_code


def programme_text(event):
    lines = []
    works = event.get('workPerformed') or []
    if isinstance(works, dict):
        works = [works]
    for work in works:
        if not isinstance(work, dict):
            continue
        author = work.get('author') or {}
        composer = clean_text(author.get('name')) if isinstance(author, dict) else ''
        name = clean_text(work.get('name'))
        if name:
            lines.append(f'{composer} - {name}' if composer else name)
    return '\n'.join(lines)


def make_record(event, page_url):
    title = clean_text(event.get('name'))
    start = clean_text(event.get('startDate'))
    try:
        start_at = datetime.fromisoformat(start.replace('Z', '+00:00'))
    except ValueError:
        return None
    location = resolve_location(event)
    url = clean_text(event.get('url')) or page_url
    if not title or not url or not location:
        return None

    description = clean_text(event.get('description'))
    programme = programme_text(event)
    if programme and programme.casefold() not in description.casefold():
        description = '\n\n'.join(filter(None, (description, 'Programme\n' + programme)))
    venue, city, country_code = location
    return {
        'title': title,
        'date': start_at.date().isoformat(),
        'url': url,
        'time_from': start_at.strftime('%H:%M') if 'T' in start else None,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def detail_records(session, url):
    soup = get_soup(session, url)
    return [record for event in event_nodes(soup) if (record := make_record(event, url))]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = discover_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(detail_records, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape RSNO event detail',
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


class RsnoOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='rsno_org_uk',
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
    RsnoOrgUkCrawler().run()


if __name__ == '__main__':
    main()
