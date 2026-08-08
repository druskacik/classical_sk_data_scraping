import re
import unicodedata
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://saph.ba/'
API_URL = urljoin(SOURCE_URL, 'wp-json/wp/v2/pages')
SOURCE = 'Sarajevska filharmonija'
HOME_VENUE = 'Narodno pozorište Sarajevo'
HOME_CITY = 'Sarajevo'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'bs-BA,bs;q=0.9,en;q=0.7',
}

MONTHS = {
    'januar': 1, 'januara': 1,
    'februar': 2, 'februara': 2,
    'mart': 3, 'marta': 3,
    'april': 4, 'aprila': 4,
    'maj': 5, 'maja': 5,
    'juni': 6, 'juna': 6,
    'juli': 7, 'jula': 7,
    'august': 8, 'augusta': 8,
    'septembar': 9, 'septembra': 9,
    'oktobar': 10, 'oktobra': 10,
    'novembar': 11, 'novembra': 11,
    'decembar': 12, 'decembra': 12,
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def normalized(value):
    value = unicodedata.normalize('NFKD', clean_text(value)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()


def canonical_url(value):
    value = (value or '').strip().strip('\"\'')
    url = urljoin(SOURCE_URL, value)
    parsed = urlparse(url)
    if parsed.netloc not in {'saph.ba', 'www.saph.ba'}:
        return ''
    return f'{SOURCE_URL.rstrip("/")}{parsed.path.rstrip("/")}/'


def get_pages(session):
    response = session.get(API_URL, params={'per_page': 100}, timeout=60)
    response.raise_for_status()
    return response.json()


def parse_dates(text):
    results = []
    compound = re.search(r'(\d{1,2})\.\s*i\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})', text, re.I)
    if compound:
        day1, day2, month, year = map(int, compound.groups())
        for day in (day1, day2):
            try:
                results.append(date(year, month, day).isoformat())
            except ValueError:
                pass

    for day, month, year in re.findall(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', text):
        try:
            value = date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            continue
        if value not in results:
            results.append(value)

    for day, month_name, year in re.findall(
        r'(\d{1,2})\.\s*([A-Za-zčćžšđČĆŽŠĐ]+)\s+(\d{4})', text
    ):
        month = MONTHS.get(month_name.lower())
        if not month:
            continue
        try:
            value = date(int(year), month, int(day)).isoformat()
        except ValueError:
            continue
        if value not in results:
            results.append(value)
    return results


def parse_times(text):
    match = re.search(r'Vrijeme\s*:\s*(.*?)(?:\n(?:Dirigent|Solist|Lokacija|Narator)\s*:|$)', text, re.I | re.S)
    if not match:
        return [None]
    times = []
    for hour, minute in re.findall(r'(?<!\d)(2[0-3]|[01]?\d)(?::([0-5]\d))?', match.group(1)):
        value = f'{int(hour):02d}:{minute or "00"}'
        if value not in times:
            times.append(value)
    return times or [None]


def resolve_location(title, card_text):
    # Location words in long artist biographies are not event locations. Only
    # the concise calendar card and its title are authoritative here.
    content = normalized(f'{title}\n{card_text}')
    if 'tuzla' in content:
        return 'Bosanski kulturni centar TK', 'Tuzla'
    if 'vijecnic' in content:
        return 'Vijećnica', HOME_CITY
    if 'dom oruzanih snaga' in content:
        return 'Dom Oružanih snaga Bosne i Hercegovine', HOME_CITY
    if 'bosanski kulturni centar' in content:
        return 'Bosanski kulturni centar Sarajevo', HOME_CITY
    return HOME_VENUE, HOME_CITY


def description_for(title, url, pages_by_url, event_pages):
    page = pages_by_url.get(canonical_url(url))
    if not page:
        wanted = normalized(title)
        candidates = [
            item for item in event_pages
            if wanted and (wanted in normalized(item['title']['rendered'])
                           or normalized(item['title']['rendered']) in wanted)
        ]
        page = candidates[0] if len(candidates) == 1 else None
    if not page:
        return None
    soup = BeautifulSoup(page['content']['rendered'], 'html.parser')
    for unwanted in soup.select('script, style, form, nav, button'):
        unwanted.decompose()
    return clean_text(soup) or None


def card_records(card, listing_url, pages_by_url, event_pages):
    heading = card.select_one('h2.elementor-heading-title') or card.find('h3')
    title = clean_text(heading)
    text = clean_text(card)
    dates = parse_dates(text)
    if not title or not dates:
        return []

    detail_links = [canonical_url(link.get('href')) for link in card.select('a[href]')]
    detail_links = [url for url in detail_links if url and url != SOURCE_URL]
    url = detail_links[-1] if detail_links else listing_url
    description = description_for(title, url, pages_by_url, event_pages)
    venue, city = resolve_location(title, text)
    times = parse_times(text)
    combinations = zip(dates, times) if len(dates) == len(times) else (
        (event_date, event_time) for event_date in dates for event_time in times
    )
    return [{
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': event_time,
        'venue': venue,
        'city': city,
        'country_code': 'BA',
        'description': description or text,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    } for event_date, event_time in combinations]


def season_cards(soup):
    for card in soup.select('div[style]'):
        style = card.get('style', '').replace(' ', '').lower()
        if 'border:1pxsolid#188be5' in style and card.find('h3'):
            yield card


def homepage_cards(soup):
    for section in soup.select('section.elementor-inner-section'):
        text = clean_text(section)
        if re.search(r'Datum\s*:', text, re.I) and section.select_one('a[href*="saph.ba/"]'):
            heading = section.select_one('h2.elementor-heading-title')
            if heading:
                yield section


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    pages = get_pages(session)
    pages_by_url = {canonical_url(page['link']): page for page in pages}
    season_pages = [page for page in pages if page['slug'].startswith('koncertna-sezona-')]
    event_pages = [page for page in pages if page not in season_pages and page['slug'] != 'pocetna']
    records = []

    for page in season_pages:
        soup = BeautifulSoup(page['content']['rendered'], 'html.parser')
        for card in season_cards(soup):
            records.extend(card_records(card, canonical_url(page['link']), pages_by_url, event_pages))

    homepage = next((page for page in pages if page['slug'] == 'pocetna'), None)
    if homepage:
        soup = BeautifulSoup(homepage['content']['rendered'], 'html.parser')
        for card in homepage_cards(soup):
            records.extend(card_records(card, SOURCE_URL, pages_by_url, event_pages))

    unique = {}
    for record in sorted(records, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['url']
    )):
        key = (normalized(record['title']), record['date'], record['time_from'], record['venue'])
        current = unique.get(key)
        if not current or (current['url'] == SOURCE_URL and record['url'] != SOURCE_URL):
            unique[key] = record
    return list(unique.values())


class SaphBaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='saph_ba',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='BA',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        try:
            return get_concerts()
        except requests.RequestException as error:
            log_message(
                'Failed to fetch Sarajevska filharmonija pages',
                event='crawler_fetch_failed',
                level='error',
                url=API_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise


def main():
    SaphBaCrawler().run()


if __name__ == '__main__':
    main()
