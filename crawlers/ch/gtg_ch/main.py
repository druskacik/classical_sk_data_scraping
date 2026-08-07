import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.gtg.ch/'
SITEMAP_URL = f'{SOURCE_URL}page-sitemap.xml'
SOURCE = 'Grand Théâtre de Genève'
DEFAULT_VENUE = 'Grand Théâtre de Genève'
CITY = 'Genève'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-CH,fr;q=0.9,en;q=0.7',
}

MONTHS = {
    'janv': 1, 'janvier': 1, 'févr': 2, 'fevr': 2, 'février': 2,
    'fevrier': 2, 'mars': 3, 'avr': 4, 'avril': 4, 'mai': 5,
    'juin': 6, 'juil': 7, 'juillet': 7, 'août': 8, 'aout': 8,
    'sept': 9, 'septembre': 9, 'oct': 10, 'octobre': 10,
    'nov': 11, 'novembre': 11, 'déc': 12, 'dec': 12,
    'décembre': 12, 'decembre': 12,
}
MONTH_PATTERN = '|'.join(sorted(MONTHS, key=len, reverse=True))
SEASON_RE = re.compile(r'/saison-(\d{2})-(\d{2})/([^/?#]+)/?$')


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, xml=False):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'xml' if xml else 'html.parser')


def event_urls(session):
    sitemap = get_soup(session, SITEMAP_URL, xml=True)
    urls = []
    for node in sitemap.select('loc'):
        url = clean_text(node)
        match = SEASON_RE.search(urlparse(url).path)
        if match and not urlparse(url).path.startswith('/en/'):
            urls.append(url)
    return sorted(set(urls))


def season_year(url, month):
    match = SEASON_RE.search(urlparse(url).path)
    if not match:
        return None
    start_year = 2000 + int(match.group(1))
    return start_year if month >= 8 else start_year + 1


def valid_date(year, month, day):
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_current_dates(soup, url):
    performances = []
    for item in soup.select('.module2 .items .item'):
        text = clean_text(item)
        match = re.search(
            rf'(\d{{1,2}})\s+({MONTH_PATTERN})\.?\s*[–-]\s*(\d{{1,2}})[:h](\d{{2}})',
            text,
            re.I,
        )
        if not match:
            continue
        month = MONTHS[match.group(2).lower().rstrip('.')]
        event_date = valid_date(season_year(url, month), month, int(match.group(1)))
        if event_date:
            performances.append((event_date, f'{int(match.group(3)):02d}:{match.group(4)}'))
    return performances


def parse_archived_dates(info, url):
    performances = []
    # Archived pages remove the ticket widgets but retain dated programme lines.
    # Treat each rendered line independently so creation dates elsewhere in the
    # biography/distribution do not become performances.
    for line in clean_text(info).splitlines():
        if len(line) > 240 or not re.search(rf'\b(?:{MONTH_PATTERN})\b', line, re.I):
            continue
        time_match = re.search(r'(?:[–-]|\bà)\s*(\d{1,2})\s*h\s*(\d{2})?', line, re.I)
        if not time_match:
            continue
        prefix = line[:time_match.start()]
        year_matches = list(re.finditer(r'\b(20\d{2})\b', prefix))
        year = int(year_matches[-1].group(1)) if year_matches else None
        if year:
            prefix = prefix[:year_matches[-1].start()]

        month_matches = list(re.finditer(rf'\b({MONTH_PATTERN})\b\.?', prefix, re.I))
        previous_end = 0
        for month_match in month_matches:
            month = MONTHS[month_match.group(1).lower().rstrip('.')]
            day_text = prefix[previous_end:month_match.start()]
            days = [int(value) for value in re.findall(r'\b(\d{1,2})\b', day_text)]
            event_year = year or season_year(url, month)
            for day in days:
                event_date = valid_date(event_year, month, day)
                if event_date:
                    minute = time_match.group(2) or '00'
                    performances.append((event_date, f'{int(time_match.group(1)):02d}:{minute}'))
            previous_end = month_match.end()
    return performances


def extract_venue(info):
    lines = [line.strip(' >') for line in clean_text(info).splitlines() if line.strip()]
    candidates = []
    for line in lines:
        line = re.sub(r'^(?:au|à la|a la|aux)\s+', '', line, flags=re.I)
        if re.match(
            r'^(?:Grand Théâtre|Bâtiment des Forces Motrices|Comédie de Genève|'
            r'Pavillon de la danse|Théâtre de|Salle |Victoria Hall|Cathédrale |'
            r'Alhambra|Lancy|La Bâtie|Musée |Conservatoire)',
            line,
            re.I,
        ) and not re.search(r'\b(?:créé|dernière fois|direction|durée)\b', line, re.I):
            candidates.append(line)
    if candidates:
        return min(candidates, key=len)
    return DEFAULT_VENUE


def make_records(url, soup):
    title = clean_text(soup.select_one('h1.titre') or soup.select_one('h1'))
    info = soup.select_one('.module15 .right .content') or soup.select_one('.module15')
    description = clean_text(info) or None
    if not title or not info:
        return []

    performances = parse_current_dates(soup, url) or parse_archived_dates(info, url)
    venue = extract_venue(info)
    records = []
    for event_date, event_time in sorted(set(performances)):
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': event_time,
            'venue': venue,
            'city': CITY,
            'country_code': 'CH',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(make_records(url, future.result()))
            except (requests.RequestException, ValueError) as error:
                log_message(
                    'Failed to scrape GTG event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    unique = {
        (record['title'], record['date'], record['time_from'], record['venue']): record
        for record in records
    }
    return sorted(
        unique.values(),
        key=lambda record: (record['date'], record['time_from'] or '', record['title'], record['url']),
    )


class GtgChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='gtg_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
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
    GtgChCrawler().run()


if __name__ == '__main__':
    main()
