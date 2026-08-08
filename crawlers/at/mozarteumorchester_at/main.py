import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.mozarteumorchester.at/'
SOURCE = 'Mozarteumorchester Salzburg'
PROGRAMME_URL = f'{SOURCE_URL}termine/'
ARCHIVE_URLS = (
    f'{PROGRAMME_URL}terminarchiv-2026-27/',
    f'{PROGRAMME_URL}terminarchiv-2025-26/',
)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'jän': 1, 'jänner': 1, 'jan': 1, 'januar': 1, 'feb': 2, 'februar': 2,
    'mär': 3, 'märz': 3, 'apr': 4, 'april': 4, 'mai': 5, 'jun': 6,
    'juni': 6, 'jul': 7, 'juli': 7, 'aug': 8, 'august': 8, 'sep': 9,
    'sept': 9, 'september': 9, 'okt': 10, 'oktober': 10, 'nov': 11,
    'november': 11, 'dez': 12, 'dezember': 12,
}

DATE_RE = re.compile(
    r'(?<![-\d])(\d{1,2})\.?(?:\s+)(JÄN(?:NER)?|JAN(?:UAR)?|FEB(?:RUAR)?|'
    r'MÄRZ?|APR(?:IL)?|MAI|JUNI?|JULI?|AUG(?:UST)?|SEPT?(?:EMBER)?|'
    r'OKT(?:OBER)?|NOV(?:EMBER)?|DEZ(?:EMBER)?)\.?\s+(20\d{2})',
    re.I,
)
TIME_RE = re.compile(r'(?<!\d)([01]?\d|2[0-3])[.:](\d{2})(?!\d)')

CITY_BY_VENUE = {
    'STIFTUNG MOZARTEUM': 'Salzburg',
    'GROSSES FESTSPIELHAUS': 'Salzburg',
    'HAUS FÜR MOZART': 'Salzburg',
    'LANDESTHEATER': 'Salzburg',
    'SALZBURGER LANDESTHEATER': 'Salzburg',
    'FELSENREITSCHULE': 'Salzburg',
    'ORCHESTERHAUS': 'Salzburg',
    'STADT:BIBLIOTHEK SALZBURG': 'Salzburg',
    'EUROPARK SALZBURG': 'Salzburg',
    'DIE BACHSCHMIEDE': 'Wals-Siezenheim',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def direct_headings(matrix):
    headings = []
    for module in matrix.find_all(recursive=False):
        heading = module.find(['h1', 'h2', 'h3', 'h4'], recursive=False)
        if heading:
            headings.append((heading.name, clean_text(heading)))
    return headings


def parse_date_match(match):
    month = MONTHS.get(match.group(2).lower().rstrip('.'))
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def resolve_location(venue):
    normalized = clean_text(venue).upper()
    for marker, city in CITY_BY_VENUE.items():
        if marker in normalized:
            return clean_text(venue), city, 'AT'
    return None


def matrix_records(matrix, page_url):
    headings = direct_headings(matrix)
    dated_indexes = [i for i, (_, text) in enumerate(headings) if DATE_RE.search(text)]
    if not dated_indexes:
        return []

    first_date = dated_indexes[0]
    title_parts = [text for tag, text in headings[:first_date] if tag == 'h2']
    title = ' – '.join(dict.fromkeys(title_parts))

    venue = ''
    for tag, text in headings[first_date + 1:]:
        if tag in ('h3', 'h4') and not DATE_RE.search(text):
            venue = text
            break
    location = resolve_location(venue)
    if not title or not location:
        return []

    description_parts = []
    for module in matrix.find_all(recursive=False):
        classes = module.get('class') or []
        if 'j-text' in classes:
            text = clean_text(module)
            if text and text not in description_parts:
                description_parts.append(text)
    description = '\n\n'.join(description_parts) or None

    occurrences = []
    for index in dated_indexes:
        text = headings[index][1]
        matches = list(DATE_RE.finditer(text))
        for match in matches:
            event_date = parse_date_match(match)
            if event_date:
                times = [f'{int(h):02d}:{minute}' for h, minute in TIME_RE.findall(text)]
                occurrences.append((event_date, times))

    # Some entries put one or more times in a separate heading immediately
    # after the date (for example "15.00 und 16.00").
    if occurrences and not any(times for _, times in occurrences):
        following = headings[dated_indexes[-1] + 1][1] if dated_indexes[-1] + 1 < len(headings) else ''
        separate_times = [f'{int(h):02d}:{minute}' for h, minute in TIME_RE.findall(following)]
        if separate_times:
            occurrences = [(event_date, separate_times) for event_date, _ in occurrences]

    records = []
    venue, city, country_code = location
    for event_date, times in occurrences:
        for time_from in times or [None]:
            records.append({
                'title': title,
                'date': event_date,
                'url': page_url,
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': country_code,
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def parse_page(html, page_url):
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for matrix in soup.select('#content_area [id^="cc-matrix-"]'):
        records.extend(matrix_records(matrix, page_url))
    return records


class MozarteumorchesterAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mozarteumorchester_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        records = []
        for url in (PROGRAMME_URL, *ARCHIVE_URLS):
            try:
                response = session.get(url, timeout=45)
                response.raise_for_status()
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Mozarteumorchester programme page',
                    event='crawler_fetch_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            records.extend(parse_page(response.text, url))

        return sorted(
            records,
            key=lambda item: (
                item['date'], item['time_from'] or '', item['title'], item['venue']
            ),
        )


def main():
    MozarteumorchesterAtCrawler().run()


if __name__ == '__main__':
    main()
