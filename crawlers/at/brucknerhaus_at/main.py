import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.brucknerhaus.at/'
PROGRAM_URL = urljoin(SOURCE_URL, 'programm/veranstaltungen')
SOURCE = 'Brucknerhaus Linz'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        value = value.get_text('\n', strip=True)
    text = str(value).replace('\xad', '').replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def listing_urls(session):
    urls = set()
    page = 1
    while True:
        url = PROGRAM_URL if page == 1 else f'{PROGRAM_URL}?page={page}'
        soup = get_soup(session, url)
        page_urls = {
            urljoin(SOURCE_URL, link.get('href'))
            for link in soup.select('.event__element a[href*="/programm/veranstaltungen/"]')
            if link.get('href')
        }
        new_urls = page_urls - urls
        urls.update(page_urls)
        next_link = soup.select_one('a.pagination__next')
        if not next_link or not new_urls:
            break
        page += 1
    return sorted(urls)


def date_from_url(url):
    range_match = re.search(r'(\d{2})\.(\d{2})\.-\d{2}\.\d{2}\.(\d{4})', url)
    if range_match:
        day, month, year = map(int, range_match.groups())
    else:
        matches = re.findall(r'(\d{2})\.(\d{2})\.(\d{4})', url)
        if not matches:
            return None
        day, month, year = map(int, matches[-1])
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_location(soup):
    location = soup.select_one('.event-sujet__location')
    if not location:
        return None, None, None
    values = [clean_text(span) for span in location.find_all('span', recursive=False)]
    values = [value for value in values if value]
    time_from = None
    if values and re.fullmatch(r'\d{1,2}:\d{2}', values[0]):
        time_from = values.pop(0).zfill(5)
    if not values:
        return time_from, None, None

    # Event pages list time, one or more venue components, then the city.
    # Brucknerhaus-only entries sometimes omit the otherwise constant city.
    # The programme is a Linz event calendar. Some cards spell out Linz while
    # others end with a venue/campus component (for example "Lissfeld").
    city = 'Linz'
    if values[-1] == city:
        values.pop()
    values = [value for value in values if value != '-']
    venue = ', '.join(values) if values else None
    return time_from, venue, city


def parse_detail(url, soup):
    name = clean_text(soup.select_one('.event-sujet__name'))
    subtitle = clean_text(soup.select_one('.event-sujet__subline'))
    title = f'{name} – {subtitle}' if name and subtitle else name or subtitle
    event_date = date_from_url(url)
    time_from, venue, city = parse_location(soup)

    description_parts = []
    for block in soup.select('.grid-event-detail .section-main .headertext'):
        text = clean_text(block)
        if text and text not in description_parts:
            description_parts.append(text)
    description = '\n\n'.join(description_parts) or None

    if not all((title, event_date, url, venue, city)):
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'AT',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_detail(url, future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class BrucknerhausAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='brucknerhaus_at',
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
    BrucknerhausAtCrawler().run()


if __name__ == '__main__':
    main()
