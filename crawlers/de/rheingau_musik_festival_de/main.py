import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.rheingau-musik-festival.de/startseite'
PROGRAMME_URL = 'https://www.rheingau-musik-festival.de/programm-karten/programmuebersicht'
SOURCE = 'Rheingau Musik Festival'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def detail_urls(session):
    soup = get_soup(session, PROGRAMME_URL)
    return sorted({
        urljoin(PROGRAMME_URL, link['href'].split('#', 1)[0])
        for link in soup.select('a[href*="/programmuebersicht/detail/"][href]')
    })


def parse_date(soup):
    # Detail-page titles include the otherwise omitted four-digit year.
    page_title = clean_text(soup.title)
    match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', page_title)
    if not match:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1))).isoformat()
    except ValueError:
        return None


def description_text(header, soup):
    parts = []

    # The unclassed header paragraph contains the artists. Keeping it is useful
    # context for programme extraction without mixing ticket text into fields.
    for paragraph in header.select('p:not([class])'):
        value = clean_text(paragraph)
        if value:
            parts.append(value)

    programme = soup.select_one('.ahz-event-detail-programm-margin-bottom')
    if programme:
        value = clean_text(programme)
        value = re.sub(r'\n?Programmheft\s*$', '', value).strip()
        if value:
            parts.append(value)

    # Editorial descriptions live in a half-width column in the first content
    # container following the event header. Ignore later venue descriptions.
    content = programme.find_parent('div', class_='container') if programme else None
    if content:
        for column in content.select('.row > .col-12.col-md-6'):
            value = clean_text(column)
            if value and not any(marker in value for marker in (
                'Weitere Informationen zur Veranstaltung', 'Mehr erfahren',
                'Zur Podcast-Folge',
            )):
                parts.append(value)

    unique = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return '\n\n'.join(unique) or None


def parse_detail(soup, url):
    header = soup.select_one('.event-detail-header')
    if not header:
        return None

    title_node = header.select_one('h3')
    title = clean_text(title_node)
    event_date = parse_date(soup)

    date_text = next(
        (clean_text(node) for node in header.select('p.fw-bold') if 'Uhr' in clean_text(node)),
        '',
    )
    time_match = re.search(r'(\d{1,2}):(\d{2})\s*Uhr', date_text)
    time_from = f'{int(time_match.group(1)):02d}:{time_match.group(2)}' if time_match else None

    location = ''
    for paragraph in header.select('p.fw-bold'):
        value = clean_text(paragraph).replace('\n', ' ')
        if 'Uhr' not in value and ',' in value:
            location = value
    venue, separator, city = location.rpartition(',')
    venue, city = venue.strip(), city.strip()

    if not all((title, event_date, url, venue, separator, city)):
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'DE',
        'description': description_text(header, soup),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount('https://', HTTPAdapter(
        pool_connections=8,
        pool_maxsize=8,
        max_retries=Retry(
            total=3,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 502, 503, 504),
        ),
    ))
    urls = detail_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_detail(future.result(), url)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Rheingau Musik Festival event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)

    return sorted(records, key=lambda record: (
        record['date'], record['time_from'] or '', record['title'], record['url']
    ))


class RheingauMusikFestivalDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='rheingau_musik_festival_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
    RheingauMusikFestivalDeCrawler().run()


if __name__ == '__main__':
    main()
