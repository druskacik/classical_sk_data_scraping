import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.brandenburg.com.au/'
SOURCE = 'Australian Brandenburg Orchestra'
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        'Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}


def clean_text(node):
    if not node:
        return ''
    text = node.get_text('\n', strip=True) if hasattr(node, 'get_text') else str(node)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def canonical_url(value):
    parts = urlsplit(urljoin(SOURCE_URL, value))
    path = parts.path.rstrip('/') + '/'
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))


def get_soup(session, url, parser='html.parser'):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, parser)


def discover_urls(session):
    sitemap = get_soup(session, SITEMAP_URL, 'xml')
    return {
        canonical_url(node.get_text(strip=True))
        for node in sitemap.select('loc')
        if '/live-concerts/events/' in node.get_text(strip=True)
    }


def description_from(soup):
    parts = []
    hero_intro = soup.select_one('.component-page-hero .intro, .component-page-hero .rte')
    program = soup.select_one('.component-concert-program')
    if hero_intro:
        parts.append(clean_text(hero_intro))
    if program:
        parts.append(clean_text(program))

    for section in soup.select('main > section.component:not([class*="component-"])'):
        text = clean_text(section)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(part for part in parts if part) or None


def detail_records(session, url):
    soup = get_soup(session, url)
    title = re.sub(r'\s+', ' ', clean_text(
        soup.select_one('.component-page-hero h1, main h1')
    )).strip()
    if not title:
        return []

    description = description_from(soup)
    records = []
    for location in soup.select('.component-dates-times-view .find-tickets'):
        city = clean_text(location.select_one('.find-ticket .location'))
        venue = clean_text(location.select_one('.venue-wrapper h4'))
        if not city or not venue:
            continue

        for ticket in location.select('.ticket-wrapper .ticket'):
            date_text = clean_text(ticket.select_one('.date'))
            try:
                date = datetime.strptime(date_text, '%a, %d %b, %Y').date().isoformat()
            except ValueError:
                continue

            time_text = clean_text(ticket.select_one('.time'))
            try:
                time_from = datetime.strptime(time_text, '%I:%M %p').strftime('%H:%M')
            except ValueError:
                time_from = None

            records.append({
                'title': title,
                'date': date,
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


class BrandenburgComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='brandenburg_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
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
        urls = discover_urls(session)
        records = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(detail_records, session, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    records.extend(future.result())
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape Brandenburg concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(records, key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['venue']
        ))


def main():
    BrandenburgComAuCrawler().run()


if __name__ == '__main__':
    main()
