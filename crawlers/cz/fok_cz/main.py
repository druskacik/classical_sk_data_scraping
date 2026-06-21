import re
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.fok.cz'
PROGRAM_URL = f'{BASE_URL}/program'
SOURCE_NAME = 'Symfonický orchestr hl. m. Prahy FOK'
SOURCE_URL = BASE_URL

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

MONTHS = {
    'ledna': '01',
    'února': '02',
    'brezna': '03',
    'března': '03',
    'dubna': '04',
    'května': '05',
    'kvetna': '05',
    'června': '06',
    'cervna': '06',
    'července': '07',
    'cervence': '07',
    'srpna': '08',
    'září': '09',
    'zari': '09',
    'října': '10',
    'rijna': '10',
    'listopadu': '11',
    'prosince': '12',
}


def clean_text(text):
    if not text:
        return ''
    text = unescape(text).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def get_page_count(soup):
    pages = [0]
    for link in soup.select('a[href*="page="]'):
        match = re.search(r'[?&]page=(\d+)', link.get('href', ''))
        if match:
            pages.append(int(match.group(1)))
    return max(pages) + 1


def get_detail_urls(session):
    first_page = get_soup(session, PROGRAM_URL)
    page_count = get_page_count(first_page)
    urls = []
    seen = set()

    for page in range(page_count):
        soup = first_page if page == 0 else get_soup(session, f'{PROGRAM_URL}?page={page}')
        for card in soup.select('.Program-item'):
            title_link = card.select_one('.Program-title a[href]')
            time_el = card.select_one('time')
            if not title_link or not time_el:
                continue

            url = urljoin(BASE_URL, title_link.get('href'))
            if url not in seen:
                seen.add(url)
                urls.append(url)

    return urls


def parse_date_time(text):
    text = clean_text(text).lower()
    match = re.search(
        r'(\d{1,2})\.\s*([a-zá-žěščřžýíéúůóďťň]+|\d{1,2})\.?\s+(\d{4}).*?(\d{1,2}:\d{2})',
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, None

    day, month_value, year, time_from = match.groups()
    month = month_value.zfill(2) if month_value.isdigit() else MONTHS.get(month_value)
    if not month:
        return None, None

    return f'{year}-{month}-{day.zfill(2)}', time_from


def extract_description(soup):
    parts = []

    repertoire = soup.select_one('.Program-Detail-info .repertoar')
    if repertoire:
        parts.append(clean_text(repertoire.get_text('\n', strip=True)))

    body = soup.select_one('.Program-Detail-texts')
    if body:
        parts.append(clean_text(body.get_text('\n', strip=True)))

    return clean_text('\n\n'.join(part for part in parts if part)) or None


def extract_ticket_rows(soup, url):
    title_el = soup.select_one('h1.Program-Detail-title') or soup.select_one('h1')
    title = clean_text(title_el.get_text(' ', strip=True)) if title_el else None
    description = extract_description(soup)
    rows = []

    for ticket in soup.select('.Program-Detail-tickets .Ticket'):
        time_el = ticket.select_one('.Ticket-date time')
        venue_el = ticket.select_one('.Ticket-info .Ticket-place')
        date, time_from = parse_date_time(time_el.get_text(' ', strip=True) if time_el else '')
        venue = clean_text(venue_el.get_text(' ', strip=True)) if venue_el else None
        if not title or not date:
            continue

        rows.append({
            'title': title,
            'date': date,
            'time_from': time_from,
            'time_to': None,
            'url': url,
            'venue': venue,
            'city': 'Praha',
            'type': None,
            'description': description,
        })

    return rows


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)

    concerts = []
    for url in get_detail_urls(session):
        try:
            soup = get_soup(session, url)
        except requests.RequestException as exc:
            print(f'Failed to scrape {url}: {exc}')
            continue

        concerts.extend(extract_ticket_rows(soup, url))

    return concerts


class FokCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='fok_cz',
        source=SOURCE_NAME,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'time_from',
            'time_to',
            'url',
            'venue',
            'city',
            'type',
            'description',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE_NAME),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    FokCrawler().run()


if __name__ == '__main__':
    main()
