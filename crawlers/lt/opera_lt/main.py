import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urldefrag, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.opera.lt/'
SOURCE = 'Lietuvos nacionalinis operos ir baleto teatras'
CITY = 'Vilnius'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'lt-LT,lt;q=0.9,en;q=0.7',
}

MONTHS = {
    'sausis': 1,
    'vasaris': 2,
    'kovas': 3,
    'balandis': 4,
    'gegužė': 5,
    'birželis': 6,
    'liepa': 7,
    'rugpjūtis': 8,
    'rugsėjis': 9,
    'spalis': 10,
    'lapkritis': 11,
    'gruodis': 12,
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


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def production_urls(session):
    soup = get_soup(session, SOURCE_URL)
    urls = set()
    for link in soup.select('a[href*="#show"]'):
        href = link.get('href')
        if href and '/-repertuaras/' in href:
            urls.add(urldefrag(urljoin(SOURCE_URL, href))[0])
    return sorted(urls)


def description_from_page(soup):
    parts = []

    # Creator credits are essential input for later composer/work extraction.
    credits = []
    for item in soup.select('#operaevents_team .team_item'):
        role = clean_text(item.select_one('.role'))
        creators = clean_text(item.select_one('.creators'))
        if role and creators:
            credits.append(f'{role}: {creators}')
    if credits:
        parts.append('Kūrėjai\n' + '\n'.join(credits))

    for selector in (
        '#operaevents_description .oe_description',
        '#operaevents_description .duration',
    ):
        text = clean_text(soup.select_one(selector))
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def location(item):
    venue = clean_text(item.select_one('.location_side'))
    if not venue:
        return None, None

    # Every currently published performance is at an explicitly named LNOBT
    # space. Do not extend this default to a future touring location.
    if re.search(r'\bLNOBT\b', venue, re.IGNORECASE):
        return venue, CITY
    return None, None


def records_from_page(url, soup):
    title = clean_text(soup.select_one('h1.main_title'))
    description = description_from_page(soup)
    records = []
    year = None
    month = None

    shows = soup.select_one('#operaevents_shows')
    if not title or not shows:
        return records

    for node in shows.select('.oe_shows_year_separator, .oe_shows_group'):
        if 'oe_shows_year_separator' in (node.get('class') or []):
            value = clean_text(node.select_one('.year_title'))
            year = int(value) if re.fullmatch(r'\d{4}', value) else None
            continue

        month_name = clean_text(node.select_one('.group_title')).lower()
        month = MONTHS.get(month_name)
        if year is None or month is None:
            continue

        for item in node.select(':scope > .oe_show_item'):
            day_text = clean_text(item.select_one('.day_side'))
            time_text = clean_text(item.select_one('.time_side'))
            venue, city = location(item)
            if not day_text.isdigit() or not venue or not city:
                continue
            try:
                event_date = date(year, month, int(day_text)).isoformat()
            except ValueError:
                continue
            time_from = time_text if re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', time_text) else None
            show_id = (item.get('id') or '').removeprefix('oe_show')
            event_url = f'{url}#show{show_id}_openperformers' if show_id else url
            records.append({
                'title': title,
                'date': event_date,
                'url': event_url,
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': 'LT',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=('GET',),
    )
    session.mount('https://', HTTPAdapter(max_retries=retry, pool_maxsize=10))
    urls = production_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(records_from_page(url, future.result()))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape production detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class OperaLtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='opera_lt',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='LT',
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
    OperaLtCrawler().run()


if __name__ == '__main__':
    main()
