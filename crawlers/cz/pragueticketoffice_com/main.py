import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


BASE_URL = 'https://www.pragueticketoffice.com'
SOURCE_URL = f'{BASE_URL}/'
LISTING_URL = f'{BASE_URL}/events/classical-music'
SOURCE = 'Prague Ticket Office'
DEFAULT_CITY = 'Prague'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    value = value.replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def element_text(parent, selector):
    element = parent.select_one(selector)
    if not element:
        return None
    return clean_text(element.get_text(' ', strip=True)) or None


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def performance_datetime(url):
    match = re.search(r'/(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})/?$', urlsplit(url).path)
    if not match:
        return None, None
    date, hour, minute = match.groups()
    return date, f'{hour}:{minute}'


def canonical_event_url(url):
    parsed = urlsplit(url)
    path = re.sub(r'/\d{4}-\d{2}-\d{2}-\d{2}-\d{2}/?$', '', parsed.path)
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def detail_description(session, performance_url, cache):
    event_url = canonical_event_url(performance_url)
    if event_url in cache:
        return cache[event_url]

    soup = get_soup(session, event_url)
    description_container = None
    for heading in soup.select('.col-6 h2'):
        candidate = heading.find_parent('div', class_='col-6')
        if candidate and candidate.select_one('h3'):
            description_container = candidate
            break

    if description_container:
        for unwanted in description_container.select('script, style, form, img'):
            unwanted.decompose()
        description = clean_text(description_container.get_text('\n', strip=True))
    else:
        description = ''

    cache[event_url] = description or None
    return cache[event_url]


def listing_fallback_description(card):
    parts = [
        element_text(card, '.evText h4'),
        element_text(card, '.evText p'),
    ]
    return clean_text('\n'.join(part for part in parts if part)) or None


def extract_cards(soup, session, description_cache):
    concerts = []
    for card in soup.select('article.ev'):
        title_link = card.select_one('.evText h3 a[href]')
        if not title_link:
            continue

        url = urljoin(BASE_URL, title_link.get('href'))
        date, time_from = performance_datetime(url)
        title = clean_text(title_link.get_text(' ', strip=True))
        if not title or not date:
            continue

        fallback = listing_fallback_description(card)
        try:
            description = detail_description(session, url, description_cache) or fallback
        except requests.RequestException as exc:
            log_message('Failed to scrape concert detail', event='crawler_item_failed', level='warning', url=url, error_type=type(exc).__name__, error_message=str(exc))
            description = fallback

        concerts.append(
            {
                'title': title,
                'date': date,
                'url': url,
                'time_from': time_from,
                'venue': element_text(card, '.loc'),
                'city': DEFAULT_CITY,
                'country_code': 'CZ',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )
    return concerts


def ajax_soups(session, url):
    response = session.get(urljoin(BASE_URL, url), timeout=30)
    response.raise_for_status()
    payload = response.json()
    return [
        BeautifulSoup(update.get('content', ''), 'html.parser')
        for update in payload.get('data', [])
        if update.get('content')
    ]


def ajax_url(soups, url_fragment):
    for soup in soups:
        link = soup.select_one(f'a[data-ajax-url*="{url_fragment}"]')
        if link:
            return link.get('data-ajax-url')
    return None


def scrape_month(session, initial_soups, description_cache):
    concerts = []
    soups = initial_soups
    visited_load_urls = set()

    while True:
        for soup in soups:
            concerts.extend(extract_cards(soup, session, description_cache))

        load_url = ajax_url(soups, 'LoadMorePrograms')
        if not load_url or load_url in visited_load_urls:
            break
        visited_load_urls.add(load_url)
        soups = ajax_soups(session, load_url)

    return concerts


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    listing_soup = get_soup(session, LISTING_URL)
    description_cache = {}
    concerts = scrape_month(session, [listing_soup], description_cache)

    next_month_url = ajax_url([listing_soup], 'ProgramApplyFilter')
    visited_month_urls = set()
    while next_month_url and next_month_url not in visited_month_urls:
        visited_month_urls.add(next_month_url)
        month_soups = ajax_soups(session, next_month_url)
        concerts.extend(scrape_month(session, month_soups, description_cache))
        next_month_url = ajax_url(month_soups, 'ProgramApplyFilter')

    unique = {
        (concert['date'], concert['time_from'], concert['url']): concert
        for concert in concerts
    }
    return sorted(
        unique.values(),
        key=lambda concert: (
            concert['date'],
            concert['time_from'] or '',
            concert['title'],
        ),
    )


class PragueTicketOfficeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='pragueticketoffice_com',
        source=SOURCE,
        source_url=SOURCE_URL,
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
        dedupe_subset=['date', 'time_from', 'url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    PragueTicketOfficeCrawler().run()


if __name__ == '__main__':
    main()
