import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


BASE_URL = 'https://www.saldovo-divadlo.cz'
PROGRAM_URL = f'{BASE_URL}/program'
CONCERTS_URL = f'{BASE_URL}/symfonicke-koncerty'
SOURCE = 'Divadlo F. X. Šaldy'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def clean_text(value):
    if not value:
        return ''
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def production_slug(url):
    """Return the stable production slug from either form of detail URL."""
    parts = [part for part in urlparse(url).path.split('/') if part]
    try:
        marker = parts.index('r')
    except ValueError:
        return ''
    tail = parts[marker + 1:]
    if tail and tail[0].isdigit():
        tail = tail[1:]
    return tail[0].lower() if tail else ''


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    response.encoding = 'utf-8'
    return BeautifulSoup(response.text, 'html.parser')


def extract_concert_slugs(soup):
    return {
        production_slug(urljoin(BASE_URL, link.get('href')))
        for link in soup.select('a[href*="/program/detail-predstaveni/r/"]')
        if production_slug(urljoin(BASE_URL, link.get('href')))
    }


def extract_month_urls(soup):
    urls = set()
    for link in soup.select('a[href*="/program/r/"]'):
        url = urljoin(BASE_URL, link.get('href'))
        if re.search(r'/program/r/\d{2}-\d{2}--$', urlparse(url).path):
            urls.add(url)
    return sorted(urls)


def extract_month_events(soup, month_url, concert_slugs):
    month_match = re.search(r'/program/r/(\d{2})-(\d{2})--', month_url)
    if not month_match:
        return []
    year, month = 2000 + int(month_match.group(1)), int(month_match.group(2))
    events = []

    for card in soup.select('.program-item'):
        title_link = next(
            (
                link for link in card.select(
                    'a[href*="/program/detail-predstaveni/r/"]'
                )
                if clean_text(link.get_text(' ', strip=True))
            ),
            None,
        )
        if not title_link:
            continue
        url = urljoin(BASE_URL, title_link.get('href'))
        if production_slug(url) not in concert_slugs:
            continue

        title = clean_text(title_link.get_text(' ', strip=True))
        card_text = clean_text(card.get_text('\n', strip=True))
        day_match = re.search(r'(?<!\d)(\d{1,2})\.\d{1,2}\.', card_text)
        time_match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', card_text)
        if not title or not day_match:
            continue
        try:
            event_date = date(year, month, int(day_match.group(1)))
        except ValueError:
            continue

        events.append({
            'title': title,
            'date': event_date.isoformat(),
            'url': url,
            'time_from': (
                f'{int(time_match.group(1)):02d}:{time_match.group(2)}'
                if time_match else None
            ),
            'listing_description': card_text,
        })
    return events


def extract_detail(soup, fallback):
    description_parts = []
    bodies = soup.select('.stred.hlavni')
    if bodies:
        body_text = max(
            (clean_text(body.get_text('\n', strip=True)) for body in bodies),
            key=len,
        )
        if body_text:
            description_parts.append(body_text)

    facts = []
    for node in soup.select('.detail-predstaveni-role, .detail-predstaveni-person'):
        value = clean_text(node.get_text(' ', strip=True))
        if value:
            facts.append(value)
    if facts:
        description_parts.append('\n'.join(facts))

    description = clean_text('\n\n'.join(description_parts)) or fallback
    facts_text = '\n'.join(facts)
    venue_match = re.search(
        r'(?:DIVADLO|MÍSTO):\s*\n([^\n]+)', facts_text, re.IGNORECASE
    )
    venue = clean_text(venue_match.group(1)) if venue_match else None
    return description, venue


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concert_slugs = extract_concert_slugs(get_soup(session, CONCERTS_URL))
    program_soup = get_soup(session, PROGRAM_URL)
    month_urls = extract_month_urls(program_soup)

    events = []
    for month_url in month_urls:
        events.extend(
            extract_month_events(get_soup(session, month_url), month_url, concert_slugs)
        )

    details = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(get_soup, session, url): url
            for url in {event['url'] for event in events}
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                fallback = next(
                    event['listing_description'] for event in events
                    if event['url'] == url
                )
                details[url] = extract_detail(future.result(), fallback)
            except Exception as exc:
                log_message('Failed to scrape detail', event='crawler_item_failed', level='warning', url=url, error_type=type(exc).__name__, error_message=str(exc))

    records = []
    seen = set()
    for event in events:
        description, venue = details.get(
            event['url'], (event['listing_description'], None)
        )
        key = (event['title'], event['date'], event['time_from'], venue)
        if key in seen:
            continue
        seen.add(key)
        records.append({
            'title': event['title'],
            'date': event['date'],
            'url': event['url'],
            'time_from': event['time_from'],
            'venue': venue,
            'city': 'Liberec',
            'country_code': 'CZ',
            'description': description,
            'source_url': BASE_URL,
            'source': SOURCE,
        })

    return sorted(
        records,
        key=lambda item: (item['date'], item['time_from'] or '', item['title']),
    )


class SaldovoDivadloCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='saldovo_divadlo_cz',
        source=SOURCE,
        source_url=BASE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'venue',
            'city',
            'country_code',
            'description',
            'source_url',
            'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    SaldovoDivadloCrawler().run()


if __name__ == '__main__':
    main()
