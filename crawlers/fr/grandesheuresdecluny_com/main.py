import re
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.grandesheuresdecluny.com/'
CONCERTS_URL = urljoin(SOURCE_URL, 'concerts')
SOURCE = 'Les Grandes Heures de Cluny'
CITY = 'Cluny'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
}

MONTHS = {
    'janvier': 1,
    'février': 2,
    'fevrier': 2,
    'mars': 3,
    'avril': 4,
    'mai': 5,
    'juin': 6,
    'juillet': 7,
    'août': 8,
    'aout': 8,
    'septembre': 9,
    'octobre': 10,
    'novembre': 11,
    'décembre': 12,
    'decembre': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def page_year(soup):
    text = clean_text(soup.get_text(' ', strip=True))
    match = re.search(r'(?:©|Brochure|Festival)\s*(20\d{2})', text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_heading(value, year):
    match = re.search(
        r'\b(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+[I|]\s*'
        r'(\d{1,2})\s*h\s*(\d{2})\s+[I|]\s*(.+)',
        clean_text(value),
        re.IGNORECASE,
    )
    if not match or not year:
        return None, None, None
    day, month_name, hour, minute, venue = match.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return None, None, None
    try:
        event_date = date(year, month, int(day)).isoformat()
    except ValueError:
        return None, None, None
    hour, minute = int(hour), int(minute)
    if hour > 23 or minute > 59:
        return None, None, None
    venue = clean_text(venue).strip(' .|-')
    return event_date, f'{hour:02d}:{minute:02d}', venue or None


def event_link(section):
    candidates = []
    for link in section.select('a[href]'):
        href = urljoin(SOURCE_URL, link.get('href'))
        if urlparse(href).netloc != urlparse(SOURCE_URL).netloc:
            continue
        label = clean_text(link.get_text(' ', strip=True)).lower()
        candidates.append((label, href))
    for label, href in candidates:
        if label in {'en savoir plus', 'réserver'}:
            return href
    return candidates[0][1] if candidates else None


def detail_data(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.title.get_text()).split('|', 1)[0].strip() if soup.title else ''
    main = soup.select_one('main')
    description = clean_text(main.get_text('\n', strip=True)) if main else ''
    # Prices and booking instructions do not help programme extraction.
    description = re.split(
        r'\n(?:Tarif(?:s| unique)?|Réserver (?:vos billets|votre billet))\b',
        description,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    return title, description or None


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    soup = get_soup(session, CONCERTS_URL)
    year = page_year(soup)
    records = []

    # Each concert is a Wix grid section (gpDCD5); the introductory section
    # has neither a date heading nor an event link and is ignored.
    for section in soup.select('.gpDCD5'):
        lines = [clean_text(line) for line in section.get_text('\n', strip=True).splitlines()]
        lines = [line for line in lines if line]
        heading = lines[0] if lines else ''
        event_date, time_from, venue = parse_heading(heading, year)
        url = event_link(section)
        if not event_date or not venue or not url:
            continue

        try:
            title, description = detail_data(session, url)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape concert detail',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            title = lines[1] if len(lines) > 1 else ''
            description = clean_text('\n'.join(lines[1:])) or None

        if not title:
            continue
        records.append(
            {
                'title': title,
                'date': event_date,
                'url': url,
                'time_from': time_from,
                'venue': venue,
                'city': CITY,
                'country_code': 'FR',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            }
        )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class GrandesHeuresDeClunyCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='grandesheuresdecluny_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='FR',
        upload_target='classical',
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
    GrandesHeuresDeClunyCrawler().run()


if __name__ == '__main__':
    main()
