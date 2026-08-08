import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.musicaviva.com.au/'
SOURCE = 'Musica Viva Australia'
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            '', 'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December',
        )
    )
    if name
}

CITY_NAMES = (
    'Adelaide', 'Brisbane', 'Canberra', 'Darwin', 'Hobart', 'Melbourne',
    'Newcastle', 'Perth', 'Sydney', 'Townsville', 'Wollongong',
)


def clean_text(value):
    if value is None:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def discover_event_urls(xml):
    soup = BeautifulSoup(xml, 'xml')
    urls = []
    for node in soup.find_all('loc'):
        url = clean_text(node)
        path = urlparse(url).path.rstrip('/')
        if '/concert-season/' not in path:
            continue
        tail = path.rsplit('/', 1)[-1]
        if tail in {'concert-season', 'past-seasons'} or tail.startswith('concerts-20'):
            continue
        urls.append(url)
    return list(dict.fromkeys(urls))


def page_year(soup, url):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or '')
        except (TypeError, json.JSONDecodeError):
            continue
        if data.get('@type') == 'Event':
            match = re.match(r'(20\d{2})-', data.get('startDate') or '')
            if match:
                return int(match.group(1))
    match = re.search(r'/(20\d{2})/', url)
    if not match:
        match = re.search(r'/concerts-(20\d{2})/', url)
    return int(match.group(1)) if match else None


def parse_date(value, year):
    match = re.search(r'\b(\d{1,2})\s+([A-Za-z]+)\b', value)
    if not match or match.group(2).lower() not in MONTHS or not year:
        return None
    try:
        return date(year, MONTHS[match.group(2).lower()], int(match.group(1))).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*([ap])\.?m\.?(?:\b|$)', value, re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'p':
        hour += 12
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def location_for(card, heading):
    heading = clean_text(heading)
    venue = re.sub(r'\s*,?\s*(?:' + '|'.join(CITY_NAMES) + r')\s*$', '', heading, flags=re.I)
    venue = venue.strip(' ,-') or heading

    for city in CITY_NAMES:
        if re.search(rf'\b{re.escape(city)}\b', heading, re.I):
            return venue, city

    location = card.select_one('.location .content-details')
    location_text = clean_text(location)
    for city in CITY_NAMES:
        if re.search(rf'\b{re.escape(city)}\b', location_text, re.I):
            return venue, city

    # Australian addresses conventionally end in "locality STATE postcode".
    match = re.search(
        r'(?:,|\n)\s*([A-Za-z][A-Za-z .\'-]+?)\s+'
        r'(?:ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\s+\d{4}\b',
        location_text,
    )
    return (venue, match.group(1).strip()) if match else (None, None)


def description_for(soup):
    parts = []
    for selector in (
        '.event-intro-title', '.event-intro-content',
        '.event-description',
    ):
        text = clean_text(soup.select_one(selector))
        if text and text not in parts:
            parts.append(text)
    for section in soup.select('.detail-page-content .event-info'):
        text = clean_text(section)
        if text and text not in parts:
            parts.append(text)
    program = clean_text(soup.select_one('#collapseProgram .card-body'))
    if program and program not in parts:
        parts.append('Program\n' + program)
    return '\n\n'.join(parts) or None


def parse_event_page(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title = clean_text(soup.select_one('h1'))
    year = page_year(soup, url)
    description = description_for(soup)
    records = []

    for card in soup.select('#eventTarget .accordion > .card'):
        heading = card.select_one('.card-header h3')
        venue, city = location_for(card, heading)
        if not title or not venue or not city:
            continue
        sessions = card.select('.date-content')
        for session in sessions:
            event_date = parse_date(clean_text(session.select_one('.date-type')), year)
            if not event_date:
                continue
            times = session.select('.session-time') or [None]
            for time_node in times:
                records.append({
                    'title': title,
                    'date': event_date,
                    'url': url,
                    'time_from': parse_time(clean_text(time_node)),
                    'venue': venue,
                    'city': city,
                    'country_code': 'AU',
                    'description': description,
                })
    return records


class MusicavivaComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='musicaviva_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        sitemap = fetch(session, SITEMAP_URL)
        urls = discover_event_urls(sitemap.text)
        records = []

        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(fetch, session, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    response = future.result()
                    records.extend(parse_event_page(response.text, response.url))
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch Musica Viva event',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )

        return sorted(
            records,
            key=lambda item: (
                item['date'], item['time_from'] or '', item['title'], item['venue']
            ),
        )


def main():
    MusicavivaComAuCrawler().run()


if __name__ == '__main__':
    main()
