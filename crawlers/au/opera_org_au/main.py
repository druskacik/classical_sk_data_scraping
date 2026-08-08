import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://opera.org.au/'
SOURCE = 'Opera Australia'
CALENDAR_URL = urljoin(SOURCE_URL, 'calendar/')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}
TIME_RE = re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b', re.IGNORECASE)


def clean_text(element):
    if element is None:
        return ''
    text = element.get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_time(value):
    match = TIME_RE.search(value or '')
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'pm':
        hour += 12
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def parse_calendar_date(value, calendar_year, calendar_month):
    try:
        parsed = datetime.strptime(value.strip(), '%A, %B %d')
    except ValueError:
        return None
    year = calendar_year
    if parsed.month - calendar_month > 6:
        year -= 1
    elif calendar_month - parsed.month > 6:
        year += 1
    try:
        return parsed.replace(year=year).date().isoformat()
    except ValueError:
        return None


def month_coordinates(url):
    match = re.search(r'/calendar/(20\d{2})/([a-z]+)', urlparse(url).path, re.IGNORECASE)
    if not match:
        return None
    try:
        month = datetime.strptime(match.group(2), '%B').month
    except ValueError:
        return None
    return int(match.group(1)), month


def parse_performances(html, page_url, venue, city):
    coordinates = month_coordinates(page_url)
    if not coordinates:
        return []
    year, month = coordinates
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for day in soup.select('li.list-item-day'):
        date_node = day.select_one('.day-of-the-month-full')
        if date_node is None:
            continue
        event_date = parse_calendar_date(clean_text(date_node), year, month)
        if not event_date:
            continue
        for event in day.select('.oa-calendar-event'):
            link = event.select_one('.oa-event-name a[href]')
            title = clean_text(link)
            url = urljoin(SOURCE_URL, link.get('href', '')) if link else ''
            if not title or not url:
                continue
            records.append({
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': parse_time(clean_text(event.select_one('.event-time'))),
                'venue': venue,
                'city': city,
                'country_code': 'AU',
                'description': None,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def production_description(html):
    soup = BeautifulSoup(html, 'html.parser')
    main = soup.select_one('main') or soup.select_one('[role="main"]')
    if main is None:
        return None
    for unwanted in main.select(
        'script, style, form, .calendar-grid-contents, .js-hero-ticket-cta, '
        '.performance-tix-button, .button, .social-sharing-component'
    ):
        unwanted.decompose()
    return clean_text(main) or None


class OperaOrgAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='opera_org_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        # The calendar also contains musical theatre, so classification is required.
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city', 'country_code',
            'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def get_soup(self, session, url):
        response = session.get(url, timeout=60)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')

    def discover_months(self, soup):
        urls = {
            urljoin(SOURCE_URL, option.get('value', ''))
            for option in soup.select('#month_switcher option[value]')
            if month_coordinates(urljoin(SOURCE_URL, option.get('value', '')))
        }
        return sorted(urls)

    def discover_venue_cities(self, session, month_url, soup):
        locations = [
            (option.get('value'), clean_text(option))
            for option in soup.select('#js-location option[value]')
            if option.get('value')
        ]
        venue_cities = {}
        for location_slug, city in locations:
            url = f'{month_url.rstrip("/")}/location/{location_slug}'
            try:
                location_soup = self.get_soup(session, url)
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Opera Australia location calendar',
                    event='crawler_location_fetch_failed', level='warning', url=url,
                    error_type=type(error).__name__, error_message=str(error),
                )
                continue
            for option in location_soup.select('#js-venue option[value]'):
                if option.get('value'):
                    venue_cities[option.get('value')] = (clean_text(option), city)
        return venue_cities

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        root = self.get_soup(session, CALENDAR_URL)
        records = []
        for month_url in self.discover_months(root):
            month_soup = self.get_soup(session, month_url)
            for venue_slug, (venue, city) in self.discover_venue_cities(
                session, month_url, month_soup
            ).items():
                url = f'{month_url.rstrip("/")}/venue/{venue_slug}'
                try:
                    response = session.get(url, timeout=60)
                    response.raise_for_status()
                    records.extend(parse_performances(response.text, url, venue, city))
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch Opera Australia venue calendar',
                        event='crawler_venue_fetch_failed', level='warning', url=url,
                        error_type=type(error).__name__, error_message=str(error),
                    )

        descriptions = {}
        urls = sorted({record['url'] for record in records})
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(session.get, url, timeout=60): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    response = future.result()
                    response.raise_for_status()
                    descriptions[url] = production_description(response.text)
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch Opera Australia event details',
                        event='crawler_event_fetch_failed', level='warning', url=url,
                        error_type=type(error).__name__, error_message=str(error),
                    )
        for record in records:
            record['description'] = descriptions.get(record['url'])
        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['venue']
            ),
        )


def main():
    OperaOrgAuCrawler().run()


if __name__ == '__main__':
    main()
