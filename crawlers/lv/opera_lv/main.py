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


SOURCE_URL = 'https://www.opera.lv/lv/'
SOURCE = 'Latvijas Nacionālā Opera un Balets'
CALENDAR_URL = urljoin(SOURCE_URL, 'kalendars/{year}/{month}/0/list')
CITY = 'Rīga'
VENUE = 'Latvijas Nacionālā opera'

TOUR_CITIES = {
    'cēs': ('Cēsis', 'LV'),
    'ventspil': ('Ventspils', 'LV'),
    'liepāj': ('Liepāja', 'LV'),
    'rēzekn': ('Rēzekne', 'LV'),
    'jūrmal': ('Jūrmala', 'LV'),
    'tartu': ('Tartu', 'EE'),
    'tallin': ('Tallinn', 'EE'),
    'viļņ': ('Vilnius', 'LT'),
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'lv-LV,lv;q=0.9,en;q=0.7',
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


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    session.mount('https://', HTTPAdapter(max_retries=retry, pool_maxsize=16))
    return session


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def records_from_calendar(soup, year, month):
    records = []
    for article in soup.select('article.calendar-list-entry'):
        title_link = article.select_one('.calendar-list-entry__title a[href]')
        day_text = clean_text(article.select_one('.calendar-list-entry__date figure'))
        time_text = clean_text(article.select_one('.calendar-list-entry__day time'))
        time_match = re.search(r'(?<!\d)([01]\d|2[0-3]):[0-5]\d', time_text)
        if not title_link or not day_text.isdigit():
            continue
        try:
            event_date = date(year, month, int(day_text)).isoformat()
        except ValueError:
            continue

        title = clean_text(title_link)
        detail_url = urljoin(SOURCE_URL, title_link.get('href'))
        venue, city, country_code = resolve_location(article)
        if not title or not detail_url or not venue or not city:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': detail_url,
            'time_from': time_match.group(0) if time_match else None,
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': None,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def resolve_location(article):
    label = clean_text(article.select_one('.calendar-list-entry__label'))
    if 'viesizrād' not in label.lower():
        return VENUE, CITY, 'LV'

    lowered = label.lower()
    for fragment, (city, country_code) in TOUR_CITIES.items():
        if fragment in lowered:
            venue = re.sub(r'^viesizrāde\s+', '', label, flags=re.IGNORECASE).strip()
            return venue, city, country_code

    # A touring performance must never inherit the Riga home venue. Unknown
    # tour locations are skipped instead of emitting a misleading record.
    return None, None, None


def calendar_year(session, year):
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(get_soup, session, CALENDAR_URL.format(year=year, month=month)): month
            for month in range(1, 13)
        }
        for future in as_completed(futures):
            month = futures[future]
            try:
                records.extend(records_from_calendar(future.result(), year, month))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape calendar month',
                    event='crawler_item_failed',
                    level='warning',
                    url=CALENDAR_URL.format(year=year, month=month),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return records


def listing_records(session):
    # Archived calendar routes remain available. Walk backwards until two
    # consecutive wholly empty years mark the end of the site's archive.
    records = calendar_year(session, date.today().year + 1)
    empty_years = 0
    year = date.today().year
    while empty_years < 2:
        year_records = calendar_year(session, year)
        records.extend(year_records)
        empty_years = 0 if year_records else empty_years + 1
        year -= 1
    return records


def description_from_detail(soup):
    parts = []
    composer = clean_text(soup.select_one('.open-show__sub-title'))
    if composer:
        parts.append(f'Autors / komponists\n{composer}')

    credits = []
    for person in soup.select('#nav-team .open-show__team'):
        name = clean_text(person.select_one('.open-show__team__name'))
        role = clean_text(person.select_one('.open-show__team__title'))
        if name and role:
            credits.append(f'{role}: {name}')
    if credits:
        parts.append('Radošā komanda\n' + '\n'.join(credits))

    about = soup.select_one('#nav-about')
    if about:
        for unwanted in about.select('#nav-video, header, script, style'):
            unwanted.decompose()
        text = clean_text(about)
        if text:
            parts.append(text)

    contents = soup.select_one('#nav-contents')
    if contents:
        module = contents.find_parent(class_='event-module')
        text = clean_text(module)
        if text:
            parts.append(text)
    return '\n\n'.join(dict.fromkeys(parts)) or None


def get_concerts():
    session = make_session()
    records = listing_records(session)
    descriptions = {}
    urls = sorted({record['url'] for record in records})
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                descriptions[url] = description_from_detail(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape production detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    for record in records:
        record['description'] = descriptions.get(record['url'])
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class OperaLvCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='opera_lv',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='LV',
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
    OperaLvCrawler().run()


if __name__ == '__main__':
    main()
