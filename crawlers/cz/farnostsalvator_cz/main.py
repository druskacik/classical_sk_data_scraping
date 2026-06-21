import re
from datetime import date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.farnostsalvator.cz'
ORIGINAL_URL = 'https://www.salvator.farnost.cz/'
SOURCE = 'Akademická farnost Praha'
SOURCE_URL = BASE_URL
DEFAULT_CITY = 'Praha'
DEFAULT_VENUE = 'Kostel Nejsvětějšího Salvátora'
MAX_ARTICLE_PAGES = 8

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

MUSIC_PATTERN = re.compile(
    r'\b('
    r'koncert|schol\w*|sbor\w*|orchestr\w*|varhan\w*|'
    r'te\s+deum|ror[aá]t\w*|requiem|rekviem|'
    r'm[šs]e\s+v[aá]no[cč]n[ií]|zazp[ií]v\w*|zp[ií]v[aá]\w*|'
    r'doprovod[ií]\w*|jazz\w*|swing\w*'
    r')\b',
    re.IGNORECASE,
)
NON_EVENT_PATTERN = re.compile(r'\b(video|z[aá]znam|ohl[eé]dnut[ií])\b', re.IGNORECASE)

EVENT_URL_DATE_PATTERN = re.compile(r'/udalost/\d+/(20\d{2})-(\d{2})-(\d{2})/')
DATE_TIME_PATTERN = re.compile(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})(?:\s+(\d{1,2}):(\d{2}))?')
TIME_RANGE_PATTERN = re.compile(r'\b(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\b')
TIME_TEXT_PATTERN = re.compile(r'\b(?:od|v|začíná v)\s+(\d{1,2})(?::|\.)?(\d{2})?\s*(?:hodin|hod\.|h)?\b', re.IGNORECASE)


def clean_text(text):
    if not text:
        return ''

    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_date_text(text):
    match = DATE_TIME_PATTERN.search(text or '')
    if not match:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    return f'{year}-{month:02d}-{day:02d}'


def parse_time_text(text):
    match = TIME_RANGE_PATTERN.search(text or '')
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour <= 23 and minute <= 59:
            return f'{hour:02d}:{minute:02d}'
        return None

    match = TIME_TEXT_PATTERN.search(text or '')
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def parse_event_date_from_url(url):
    match = EVENT_URL_DATE_PATTERN.search(url)
    if not match:
        return None

    return f'{match.group(1)}-{match.group(2)}-{match.group(3)}'


def extract_title(soup):
    title_el = soup.select_one('h1.font-sans') or soup.select_one('h1')
    if title_el:
        return clean_text(title_el.get_text(' ', strip=True))

    if soup.title:
        return clean_text(soup.title.get_text(' ', strip=True).split(' - ', 1)[0])

    return SOURCE


def trim_description(text):
    text = clean_text(text)
    for marker in ['Související články', 'Komentáře', 'Zpět na přehled událostí']:
        if marker in text:
            text = text.split(marker, 1)[0]
    return clean_text(text) or None


def extract_description(soup):
    content = soup.select_one('.content-inner') or soup.select_one('.content.page') or soup.select_one('.two-columns-layout')
    if not content:
        content = soup.select_one('.page')

    if not content:
        return None

    content = BeautifulSoup(str(content), 'html.parser')
    for tag in content.select('script, style, iframe, img, nav, form'):
        tag.decompose()

    return trim_description(content.get_text('\n', strip=True))


def is_music_page(title, description):
    haystack = clean_text('\n'.join(part for part in [title, description] if part))
    return bool(MUSIC_PATTERN.search(haystack)) and not bool(NON_EVENT_PATTERN.search(title or ''))


def is_upcoming(date_value):
    return bool(date_value and date_value >= date.today().isoformat())


def extract_article_meta(soup):
    desc = soup.select_one('.content-inner .desc') or soup.select_one('.desc')
    desc_text = clean_text(desc.get_text(' ', strip=True)) if desc else ''
    return parse_date_text(desc_text), parse_time_text(desc_text)


def parse_article_page(session, url):
    soup = get_soup(session, url)
    title = extract_title(soup)
    date_value, time_from = extract_article_meta(soup)
    description = extract_description(soup)

    if not date_value:
        date_value = parse_date_text(description or '')
    if not time_from:
        time_from = parse_time_text(description or '')

    if not is_upcoming(date_value) or not is_music_page(title, description):
        return None

    return {
        'title': title,
        'date': date_value,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': DEFAULT_VENUE,
        'city': DEFAULT_CITY,
        'description': description,
        'type': 'concert',
    }


def parse_event_page(session, url, listing_text=''):
    soup = get_soup(session, url)
    title = extract_title(soup)
    description = extract_description(soup)
    combined_text = clean_text('\n'.join(part for part in [description, listing_text] if part))
    date_value = parse_event_date_from_url(url) or parse_date_text(combined_text)
    time_from = parse_time_text(combined_text)

    if not is_upcoming(date_value) or not is_music_page(title, combined_text):
        return None

    description = clean_text('\n\n'.join(part for part in [description, listing_text] if part)) or None
    return {
        'title': title,
        'date': date_value,
        'url': url,
        'time_from': time_from,
        'time_to': None,
        'venue': DEFAULT_VENUE,
        'city': DEFAULT_CITY,
        'description': description,
        'type': 'concert',
    }


def discover_event_links(session):
    current_year = date.today().year
    listing_urls = [
        f'{BASE_URL}/udalosti',
        f'{BASE_URL}/udalosti/budouci/{current_year}',
        f'{BASE_URL}/udalosti/budouci/{current_year + 1}',
    ]
    links = {}

    for listing_url in listing_urls:
        soup = get_soup(session, listing_url)
        for link in soup.select('a[href*="/udalost/"]'):
            href = urljoin(BASE_URL, link.get('href'))
            item = link.find_parent('li')
            listing_text = clean_text(item.get_text('\n', strip=True)) if item else clean_text(link.get_text(' ', strip=True))
            links[href] = listing_text

    return links


def extract_listing_date(item):
    text = clean_text(item.get_text(' ', strip=True))
    return parse_date_text(text)


def discover_article_links(session):
    links = []
    today = date.today().isoformat()

    for page in range(1, MAX_ARTICLE_PAGES + 1):
        path = '/clanky' if page == 1 else f'/clanky?pg={page}'
        soup = get_soup(session, urljoin(BASE_URL, path))
        page_links = []
        page_dates = []

        for link in soup.select('a.title[href*="/clanek/"]'):
            item = link.find_parent('li')
            href = urljoin(BASE_URL, link.get('href'))
            page_links.append(href)
            if item:
                parsed_date = extract_listing_date(item)
                if parsed_date:
                    page_dates.append(parsed_date)

        links.extend(page_links)
        if page > 1 and page_dates and max(page_dates) < today:
            break

    return list(dict.fromkeys(links))


def get_concerts():
    session = requests.Session()
    concerts = []

    for link in discover_article_links(session):
        concert = parse_article_page(session, link)
        if concert:
            concerts.append(concert)

    for link, listing_text in discover_event_links(session).items():
        concert = parse_event_page(session, link, listing_text)
        if concert:
            concerts.append(concert)

    deduped = []
    seen_keys = set()
    for concert in concerts:
        title_prefix = concert['title'].split('–', 1)[0].strip().casefold()
        key = (concert['date'], title_prefix)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(concert)

    return deduped


class FarnostSalvatorCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='farnostsalvator_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'time_to',
            'venue',
            'city',
            'description',
            'type',
        ],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    FarnostSalvatorCrawler().run()


if __name__ == '__main__':
    main()
