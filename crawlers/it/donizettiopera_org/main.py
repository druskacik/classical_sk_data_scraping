import re
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.donizettiopera.org/it/'
SOURCE = 'Donizetti Opera Festival'
SITEMAP_URL = urljoin(SOURCE_URL, 'page-sitemap.xml')
CITY = 'Bergamo'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ClassicalBot/1.0)'}
MONTHS = {
    'gennaio': 1, 'febbraio': 2, 'marzo': 3, 'aprile': 4,
    'maggio': 5, 'giugno': 6, 'luglio': 7, 'agosto': 8,
    'settembre': 9, 'ottobre': 10, 'novembre': 11, 'dicembre': 12,
}
DATE_RE = re.compile(
    r'(?:(?:lunedi|lunedì|martedi|martedì|mercoledi|mercoledì|giovedi|giovedì|'
    r'venerdi|venerdì|sabato|domenica)\s+)?'
    r'(?P<days>\d{1,2}(?:\s+e\s+\d{1,2})?)\s+'
    r'(?P<month>' + '|'.join(MONTHS) + r')'
    r'(?:\s+(?P<year>20\d{2}))?(?:\s*(?:ore)?\s*(?P<time>\d{1,2}[.:]\d{2}))?',
    re.IGNORECASE,
)


def clean_text(value):
    text = BeautifulSoup(str(value or ''), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def sitemap_urls(session):
    soup = BeautifulSoup(get(session, SITEMAP_URL).content, 'xml')
    urls = {loc.get_text(strip=True) for loc in soup.select('loc')}
    return sorted(url for url in urls if re.search(r'/programma-20\d{2}/?$', url))


def programme_pages(session):
    pages = {}
    for hub_url in sitemap_urls(session):
        year_match = re.search(r'programma-(20\d{2})', hub_url)
        hub_year = int(year_match.group(1)) if year_match else None
        soup = BeautifulSoup(get(session, hub_url).content, 'html.parser')
        for link in soup.select('main a[href]'):
            url = urljoin(hub_url, link['href']).split('#')[0]
            if urlparse(url).netloc == urlparse(SOURCE_URL).netloc and '/it/' in url:
                pages[url] = hub_year
    return sorted(pages.items())


def card_for_link(link):
    node = link
    while node and node.name != 'main':
        text = clean_text(node)
        if DATE_RE.search(text):
            return node
        node = node.parent
    return None


def infer_year(page_url, text):
    match = re.search(r'/(?:[^/]*-)?(20\d{2})/', page_url)
    if match:
        return int(match.group(1))
    match = re.search(r'\b(20\d{2})\b', text)
    return int(match.group(1)) if match else None


def parse_card(card, detail_url, page_url, page_year=None):
    lines = [line for line in clean_text(card).splitlines() if line]
    whole_text = '\n'.join(lines)
    matches = list(DATE_RE.finditer(whole_text))
    year = page_year or infer_year(page_url, whole_text)
    if not matches or not year:
        return []

    before = whole_text[:matches[0].start()].splitlines()
    venue_lines = [line for line in before if not re.search(r'programma|opere?\s+20\d{2}', line, re.I)]
    venue = ' – '.join(venue_lines[-2:]) if venue_lines else ''
    headings = [clean_text(item) for item in card.select('h2, h3, h4, h5, h6')]
    headings = list(dict.fromkeys(item for item in headings if item))
    title = ' – '.join(headings)
    if not title or not venue:
        return []

    description = clean_text(whole_text) or None
    records = []
    # Later date-looking strings commonly occur in the descriptive prose. The
    # leading schedule line is the authoritative performance date expression.
    for match in matches[:1]:
        event_year = int(match.group('year') or year)
        month = MONTHS[match.group('month').lower()]
        time_value = match.group('time')
        time_from = time_value.replace('.', ':') if time_value else None
        for day_value in re.findall(r'\d{1,2}', match.group('days')):
            try:
                event_date = date(event_year, month, int(day_value)).isoformat()
            except ValueError:
                continue
            records.append({
                'title': title,
                'date': event_date,
                'url': detail_url,
                'time_from': time_from,
                'venue': venue,
                'city': CITY,
                'country_code': 'IT',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    records = []
    seen_cards = set()
    for page_url, hub_year in programme_pages(session):
        try:
            soup = BeautifulSoup(get(session, page_url).content, 'html.parser')
        except requests.RequestException as error:
            log_message(
                'Failed to scrape Donizetti Opera programme page',
                event='crawler_item_failed', level='warning', url=page_url,
                error_type=type(error).__name__, error_message=str(error),
            )
            continue
        page_heading = clean_text(soup.select_one('main h1'))
        page_year_match = re.search(r'\b(20\d{2})\b', page_heading)
        page_year = int(page_year_match.group(1)) if page_year_match else hub_year
        for link in soup.select('main a[href]'):
            if 'scopri' not in clean_text(link).lower():
                continue
            detail_url = urljoin(page_url, link['href'])
            if urlparse(detail_url).path.lower().endswith(('.pdf', '.jpg', '.jpeg', '.png')):
                continue
            card = card_for_link(link)
            if not card:
                continue
            identity = (page_url, clean_text(card))
            if identity in seen_cards:
                continue
            seen_cards.add(identity)
            records.extend(parse_card(card, detail_url, page_url, page_year))
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class DonizettiOperaOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='donizettiopera_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='IT',
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
    DonizettiOperaOrgCrawler().run()


if __name__ == '__main__':
    main()
