import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.sadlerswells.com/'
SOURCE = "Sadler's Wells"
LISTING_URL = urljoin(SOURCE_URL, 'whats-on/')
CITY = 'London'

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


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def listing_urls():
    first = get_soup(LISTING_URL)
    page_numbers = [
        int(value)
        for value in (clean_text(link) for link in first.select('a.page-numbers'))
        if value.isdigit()
    ]
    last_page = max(page_numbers, default=1)
    soups = [first]
    for page_number in range(2, last_page + 1):
        soups.append(get_soup(urljoin(LISTING_URL, f'page/{page_number}/')))

    urls = set()
    for soup in soups:
        for link in soup.select('a.c-event-card__cover-link[href]'):
            url = urljoin(SOURCE_URL, link.get('href')).split('#', 1)[0]
            if re.fullmatch(r'https://www\.sadlerswells\.com/whats-on/[^/]+/', url):
                urls.add(url)
    return sorted(urls)


def parse_time(value):
    match = re.fullmatch(
        r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)', clean_text(value), re.IGNORECASE
    )
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def event_bounds(soup):
    value = clean_text(soup.select_one('.c-event-details__datetime'))
    matches = re.findall(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', value)
    if not matches:
        return None
    try:
        parsed = [
            datetime.strptime(' '.join(parts), '%d %B %Y').date()
            for parts in matches
        ]
    except ValueError:
        return None
    return min(parsed), max(parsed)


def instance_date(value, bounds):
    if not bounds:
        return None
    text = re.sub(r'^[A-Za-z]+\s+', '', clean_text(value))
    for year in range(bounds[0].year, bounds[1].year + 1):
        try:
            candidate = datetime.strptime(f'{text} {year}', '%d %B %Y').date()
        except ValueError:
            continue
        if bounds[0] <= candidate <= bounds[1]:
            return candidate.isoformat()
    return None


def event_description(soup):
    body = soup.select_one('.c-event-content__important-info')
    if not body:
        return None
    for element in body.select('script, style, .c-event-details__prices'):
        element.decompose()
    return clean_text(body) or None


def parse_event(url):
    soup = get_soup(url)
    title = clean_text(soup.select_one('h1.c-masthead__title'))
    bounds = event_bounds(soup)
    description = event_description(soup)
    if not title or not bounds:
        return []

    records = []
    for instance in soup.select('.c-event-instance'):
        event_date = instance_date(
            instance.select_one('.c-event-instance__date'), bounds
        )
        venue = clean_text(instance.select_one('.c-event-instance__venue-name'))
        if not event_date or not venue:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': parse_time(instance.select_one('.c-event-instance__time')),
            'venue': venue,
            'city': CITY,
            'country_code': 'GB',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    urls = listing_urls()
    records = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(parse_event, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    "Failed to scrape Sadler's Wells event",
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


class SadlersWellsComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='sadlerswells_com',
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
    SadlersWellsComCrawler().run()


if __name__ == '__main__':
    main()
