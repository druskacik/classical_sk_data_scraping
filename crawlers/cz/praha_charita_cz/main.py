import re
from datetime import date, datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://praha.charita.cz'
EVENTS_URL = f'{BASE_URL}/akce/'
SOURCE = 'Arcidiecézní charita Praha'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

DATE_PATTERN = re.compile(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})\b')
TIME_PATTERN = re.compile(r'\b(?:od\s+)?(\d{1,2})(?::|\.)(\d{2})\b', re.IGNORECASE)
MUSIC_PATTERN = re.compile(
    r'\b('
    r'koncert\w*|oper[aá]\w*|oper[ií]sim\w*|belcant\w*|'
    r'orchestr\w*|symfoni\w*|filharmoni\w*|varhan\w*|'
    r'komorn[ií]\w*|recit[aá]l\w*|hudebn[ií]\w*'
    r')\b',
    re.IGNORECASE,
)


def clean_text(value):
    if not value:
        return ''
    text = unescape(str(value)).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_date(value):
    match = DATE_PATTERN.search(value or '')
    if not match:
        return None
    try:
        return datetime(
            int(match.group(3)),
            int(match.group(2)),
            int(match.group(1)),
        ).strftime('%Y-%m-%d')
    except ValueError:
        return None


def parse_time(value):
    match = TIME_PATTERN.search(value or '')
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59 or (hour == 0 and minute == 0):
        return None
    return f'{hour:02d}:{minute:02d}'


def discover_event_links(session):
    soup = get_soup(session, EVENTS_URL)
    links = []

    # The wide cards are the site's current/upcoming events. Normal cards below
    # the "Proběhlé akce" heading are intentionally excluded.
    for card in soup.select('.block--post_list__item--post_wide'):
        link = card.select_one('a[href*="/akce/"]')
        if not link:
            continue
        url = urljoin(BASE_URL, link.get('href', ''))
        if url != EVENTS_URL and url not in links:
            links.append(url)

    return links


def extract_venue(description, title):
    where_match = re.search(
        r'(?:^|\n)Kde:\s*(.+?)(?=\n(?:Vstupné|Program|Kdy):|\Z)',
        description or '',
        flags=re.IGNORECASE | re.DOTALL,
    )
    if where_match:
        return clean_text(where_match.group(1))

    if re.search(r'Smetanov\w*\s+síni\s+Obecního\s+domu', description or '', re.IGNORECASE):
        return 'Smetanova síň Obecního domu'
    if re.search(r'Obecním\s+domě', title or '', re.IGNORECASE):
        return 'Obecní dům'

    return None


def parse_event(session, url):
    soup = get_soup(session, url)
    title_node = soup.select_one('h1.single__top__title') or soup.select_one('h1')
    term_node = soup.select_one('.single__top__content p')
    content_node = soup.select_one('.show_more__content')
    if not title_node or not term_node:
        return None

    title = clean_text(title_node.get_text(' ', strip=True))
    term = clean_text(term_node.get_text(' ', strip=True))
    description = clean_text(content_node.get_text('\n', strip=True)) if content_node else ''
    date_value = parse_date(term)
    haystack = clean_text(f'{title}\n{description}')

    if not date_value or date_value < date.today().isoformat() or not MUSIC_PATTERN.search(haystack):
        return None

    return {
        'title': title,
        'date': date_value,
        'url': url,
        'time_from': parse_time(term),
        'venue': extract_venue(description, title),
        'city': 'Praha',
        'country_code': 'CZ',
        'description': description or None,
        'source_url': EVENTS_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    concerts = []

    for url in discover_event_links(session):
        concert = parse_event(session, url)
        if concert:
            concerts.append(concert)

    return concerts


class PrahaCharitaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='praha_charita_cz',
        source=SOURCE,
        source_url=EVENTS_URL,
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
        dedupe_subset=['title', 'date', 'url'],
    )

    def scrape(self):
        return get_concerts()


def main():
    PrahaCharitaCrawler().run()


if __name__ == '__main__':
    main()
