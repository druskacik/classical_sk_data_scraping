import re
from datetime import date
from urllib.parse import urljoin
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.seefestspiele-moerbisch.at/'
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')
SOURCE = 'Seefestspiele Mörbisch'
VENUE = 'Seebühne Mörbisch'
CITY = 'Mörbisch am See'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'jänner': 1, 'januar': 1, 'februar': 2, 'märz': 3, 'april': 4,
    'mai': 5, 'juni': 6, 'juli': 7, 'august': 8, 'september': 9,
    'oktober': 10, 'november': 11, 'dezember': 12,
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def sitemap_pages(session):
    namespace = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
    root = ElementTree.fromstring(get_response(session, SITEMAP_URL).content)
    sitemap_urls = [node.text for node in root.findall('.//sm:sitemap/sm:loc', namespace)]
    pages = []
    for sitemap_url in sitemap_urls:
        child = ElementTree.fromstring(get_response(session, sitemap_url).content)
        pages.extend(node.text for node in child.findall('.//sm:url/sm:loc', namespace))
    return pages


def valid_date(year, month, day):
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def ticket_record(item, page_url, description):
    text = clean_text(item)
    match = re.search(
        r'^(?P<title>.+?)\s+-\s+(?P<date>\d{2}\.\d{2}\.\d{4}),\s*'
        r'Beginn:\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})',
        text,
    )
    link = item.select_one('a[href*="shop.seefestspiele-moerbisch.at/events"]')
    if not match or not link:
        return None
    day, month, year = match.group('date').split('.')
    event_date = valid_date(year, month, day)
    title = clean_text(match.group('title')).strip(' -')
    url = urljoin(page_url, link.get('href', '').strip())
    if not title or not event_date or not url:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': f'{int(match.group("hour")):02d}:{match.group("minute")}',
        'venue': VENUE,
        'city': CITY,
        'country_code': 'AT',
        'description': description or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def introductory_dates(text):
    # The event date is displayed immediately below the page heading. Limiting
    # the search avoids mistaking ticket-sale dates in the article for shows.
    intro = text[:500]
    range_match = re.search(
        r'\b(\d{1,2})\.\s*&\s*(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\s+(20\d{2})\b',
        intro,
    )
    if range_match:
        month = MONTHS.get(range_match.group(3).lower())
        if not month:
            return []
        return [
            value for value in (
                valid_date(range_match.group(4), month, range_match.group(1)),
                valid_date(range_match.group(4), month, range_match.group(2)),
            ) if value
        ]
    match = re.search(
        r'\b(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\s+(20\d{2})\b', intro
    )
    if not match:
        return []
    month = MONTHS.get(match.group(2).lower())
    value = valid_date(match.group(3), month, match.group(1)) if month else None
    return [value] if value else []


def guest_records(soup, url):
    main = soup.select_one('main')
    heading = main.select_one('h1') if main else None
    title = clean_text(heading)
    description = clean_text(main)
    if not title or not description:
        return []
    records = []
    for event_date in introductory_dates(description):
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': None,
            'venue': VENUE,
            'city': CITY,
            'country_code': 'AT',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def programme_description(session, ticket_soup):
    candidates = []
    for link in ticket_soup.select('a[href*="/programm/"][href]'):
        url = urljoin(SOURCE_URL, link.get('href', ''))
        if '/programm/gastveranstaltungen/' in url or '/infos-zum-stueck/' in url:
            candidates.append(url)
    for url in dict.fromkeys(candidates):
        try:
            soup = BeautifulSoup(get_response(session, url).text, 'html.parser')
            description = clean_text(soup.select_one('main'))
            if description:
                return description
        except requests.RequestException as error:
            log_message(
                'Failed to scrape Seefestspiele programme detail',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
    return None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    pages = sitemap_pages(session)
    ticket_pages = sorted({url for url in pages if '/tickets-kaufen/' in url})
    guest_pages = sorted({url for url in pages if '/programm/gastveranstaltungen/' in url})
    records = []

    for url in ticket_pages:
        soup = BeautifulSoup(get_response(session, url).text, 'html.parser')
        description = programme_description(session, soup)
        for item in soup.select('ul.event-list > li'):
            record = ticket_record(item, url, description)
            if record:
                records.append(record)

    def event_key(record):
        title = re.sub(r'[^a-z0-9]+', '', record['title'].lower())
        return title, record['date']

    ticket_events = {event_key(record) for record in records}
    for url in guest_pages:
        try:
            soup = BeautifulSoup(get_response(session, url).text, 'html.parser')
            for record in guest_records(soup, url):
                # A ticket listing is richer and has an exact start time. The
                # guest detail remains the description source for that listing.
                if event_key(record) not in ticket_events:
                    records.append(record)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape Seefestspiele guest event',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )

    unique = {(record['url'], record['date']): record for record in records}
    return sorted(
        unique.values(),
        key=lambda record: (record['date'], record['time_from'] or '', record['title']),
    )


class SeefestspieleMoerbischAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='seefestspiele_moerbisch_at',
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
    SeefestspieleMoerbischAtCrawler().run()


if __name__ == '__main__':
    main()
