import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.opera-lille.fr/'
SITEMAP_URL = urljoin(SOURCE_URL, 'spectacle-sitemap.xml')
SOURCE = 'Opéra de Lille'
DEFAULT_CITY = 'Lille'
DEFAULT_VENUE = 'Opéra de Lille'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9',
}

MONTHS = {
    'janvier': 1, 'janv': 1, 'jan': 1,
    'février': 2, 'févr': 2, 'fév': 2,
    'mars': 3, 'avril': 4, 'avr': 4, 'mai': 5, 'juin': 6,
    'juillet': 7, 'juil': 7, 'août': 8,
    'septembre': 9, 'sept': 9, 'octobre': 10, 'oct': 10,
    'novembre': 11, 'nov': 11, 'décembre': 12, 'déc': 12,
}

TOUR_LOCATIONS = {
    "opéra d'anvers": ('Opera Ballet Vlaanderen (Anvers)', 'Anvers', 'BE'),
    'opéra d’anvers': ('Opera Ballet Vlaanderen (Anvers)', 'Anvers', 'BE'),
    'schouwburg kortrijk': ('Schouwburg Kortrijk', 'Courtrai', 'BE'),
    'théâtre de courtrai': ('Schouwburg Kortrijk', 'Courtrai', 'BE'),
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, parser='html.parser'):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, parser)


def spectacle_urls(session):
    soup = get_soup(session, SITEMAP_URL, 'xml')
    return list(dict.fromkeys(clean_text(node) for node in soup.select('url > loc')))


def published_year(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or '{}')
        except (TypeError, json.JSONDecodeError):
            continue
        nodes = data.get('@graph', []) if isinstance(data, dict) else []
        for node in nodes:
            match = re.match(r'(\d{4})-', str(node.get('datePublished', '')))
            if match:
                return int(match.group(1))
    return None


def page_year(soup):
    header = soup.select_one('.sHeader') or soup.select_one('.spectacle-head')
    years = [int(value) for value in re.findall(r'\b20\d{2}\b', clean_text(header))]
    return years[-1] if years else published_year(soup)


def parse_date(text, default_year):
    normalized = clean_text(text).lower().replace('.', '')
    match = re.search(
        r'(\d{1,2})\s+(' + '|'.join(sorted(MONTHS, key=len, reverse=True)) + r')'
        r'(?:\s+(20\d{2}))?',
        normalized,
    )
    if not match or not (match.group(3) or default_year):
        return None
    try:
        return date(
            int(match.group(3) or default_year),
            MONTHS[match.group(2)],
            int(match.group(1)),
        ).isoformat()
    except ValueError:
        return None


def parse_time(text):
    match = re.search(r'\b(\d{1,2})\s*h\s*(\d{2})?', clean_text(text).lower())
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2) or "00"}'


def parse_location(soup):
    header = clean_text(soup.select_one('.sHeader') or soup.select_one('.spectacle-head'))
    folded = header.lower()
    for marker, location in TOUR_LOCATIONS.items():
        if marker in folded:
            return location

    # Touring productions do not provide the per-performance stop in their
    # calendar rows. They cannot safely inherit the Lille defaults.
    if 'itinérant' in folded or 'hors les murs' in folded:
        return None, None, None
    return DEFAULT_VENUE, DEFAULT_CITY, 'FR'


def description_from(soup):
    parts = []
    selectors = (
        '.infos_spectacle > .section_content',
        '.spectacle-presentation',
        '.spectacle-distribution',
    )
    for selector in selectors:
        for element in soup.select(selector):
            text = clean_text(element)
            if text and text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def parse_detail(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.select_one('.sHeader_title'))
    if not title:
        title = clean_text(soup.select_one('.spectacle-details-title h1'))
    if not title:
        title = clean_text(soup.select_one('.spectacle-head h1'))
    if not title:
        title = clean_text(soup.select_one('.spectacle-head .titre h2'))

    venue, city, country_code = parse_location(soup)
    if not title or not venue or not city:
        return []

    year = page_year(soup)
    description = description_from(soup)
    records = []
    for date_element in soup.select('.spectacle-details-date'):
        event_date = parse_date(clean_text(date_element), year)
        if not event_date:
            continue
        row = date_element.parent
        time_element = row.select_one('.spectacle-details-heure') if row else None
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': parse_time(time_element),
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = spectacle_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(parse_detail, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Opera de Lille spectacle',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(records, key=lambda row: (row['date'], row['time_from'] or '', row['title']))


class OperaLilleFrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='opera_lille_fr',
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
    OperaLilleFrCrawler().run()


if __name__ == '__main__':
    main()
