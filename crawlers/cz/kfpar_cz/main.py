import concurrent.futures
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


BASE_URL = 'https://www.kfpar.cz'
SOURCE_URL = f'{BASE_URL}/'
SOURCE = 'Komorní filharmonie Pardubice'
SITEMAP_URL = f'{BASE_URL}/sitemap.xml'
TOUR_URL = f'{BASE_URL}/cesty-2026/2027'
INDEX_URLS = [
    f'{BASE_URL}/nejblizsi-koncerty?page=1',
    f'{BASE_URL}/nejblizsi-koncerty?page=2',
    f'{BASE_URL}/program-2026/2027',
    f'{BASE_URL}/klasicke-koncerty-2026/2027',
    f'{BASE_URL}/mimoradne-koncerty-2026/2027',
    f'{BASE_URL}/klavirni-rada-2026/2027',
    f'{BASE_URL}/adventni-rada-2026',
    f'{BASE_URL}/pro-deti-a-rodice-2026/2027',
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'cs,en;q=0.8',
}

DATE_RE = re.compile(r'(?<!\d)(\d{1,2})\.\s*(\d{1,2})\.\s*(20\d{2})(?!\d)')
WORD_DATE_RE = re.compile(
    r'(?<!\d)(\d{1,2})\.\s*'
    r'(ledna|února|března|dubna|května|června|července|srpna|září|října|listopadu|prosince)'
    r'\s+(20\d{2})(?!\d)',
    re.IGNORECASE,
)
TIME_RE = re.compile(r'(?<!\d)([01]?\d|2[0-3])(?:[:.]([0-5]\d)|\s*hod(?:in)?)', re.IGNORECASE)

CITY_PATTERNS = [
    (r'\bPardubic(?:e|ích)\b', 'Pardubice'),
    (r'\bPra(?:ha|ze|hy)\b', 'Praha'),
    (r'\bBrn(?:o|ě|a)\b', 'Brno'),
    (r'\bLitomyšl(?:i)?\b', 'Litomyšl'),
    (r'\bMariánsk(?:é|ých) Láz(?:ně|ních)\b', 'Mariánské Lázně'),
    (r'\bHeřman(?:ův|ově) Měst(?:ec|ci)\b', 'Heřmanův Městec'),
    (r'\bPoděbrad(?:y|ech)\b', 'Poděbrady'),
    (r'\bŽeliv(?:e|i)?\b', 'Želiv'),
    (r'\bDobruš(?:ka|ce)\b', 'Dobruška'),
    (r'\bBeroun(?:ě)?\b', 'Beroun'),
    (r'\bÚstí nad Orlicí\b', 'Ústí nad Orlicí'),
]

VENUE_WORDS = re.compile(
    r'\b(síň|sál|dům hudby|divadlo|kostel|katedrála|klášter|zámek|palác|'
    r'rudolfinum|konzervatoř|nádvoří|hala|areál)\b',
    re.IGNORECASE,
)

MONTHS = {
    'ledna': 1, 'února': 2, 'března': 3, 'dubna': 4,
    'května': 5, 'června': 6, 'července': 7, 'srpna': 8,
    'září': 9, 'října': 10, 'listopadu': 11, 'prosince': 12,
}


def clean_text(value):
    if not value:
        return ''
    value = str(value).replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def canonical_url(url):
    parsed = urlsplit(urljoin(BASE_URL, url))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


def fetch(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response


def parse_date_match(match):
    try:
        month_text = match.group(2).lower()
        month = MONTHS.get(month_text, int(month_text) if month_text.isdigit() else 0)
        return datetime(
            int(match.group(3)), month, int(match.group(1))
        ).date().isoformat()
    except ValueError:
        return None


def parse_time(text):
    match = TIME_RE.search(text)
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2) or "00"}'


def infer_city(text):
    for pattern, city in CITY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return city
    return None


def find_location(paragraphs):
    candidates = []
    for paragraph in paragraphs:
        text = clean_text(paragraph)
        city = infer_city(text)
        if city and VENUE_WORDS.search(text) and len(text) <= 180:
            candidates.append((text.strip(' .'), city))
    return candidates[-1] if candidates else (None, None)


def date_matches(text):
    return sorted(
        [*DATE_RE.finditer(text), *WORD_DATE_RE.finditer(text)],
        key=lambda match: match.start(),
    )


def detail_records(url):
    try:
        soup = BeautifulSoup(fetch(url).text, 'html.parser')
    except Exception as error:
        log_message(
            'Failed to fetch concert candidate',
            event='crawler_item_failed',
            level='warning',
            url=url,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        return []

    if not soup.html or soup.html.get('lang') != 'cs':
        return []
    body = soup.select_one('main article.cText')
    metadata_date = soup.select_one('main .cNews__date')
    if not body or not metadata_date:
        return []

    title = clean_text(soup.title.get_text(' ', strip=True) if soup.title else '')
    description = clean_text(body.get_text('\n', strip=True))
    if not title or not description:
        return []

    lines = [clean_text(line) for line in body.get_text('\n', strip=True).splitlines()]
    venue, city = find_location(lines)
    if not venue or not city:
        return []

    dated_lines = []
    for line in lines:
        for match in date_matches(line):
            date = parse_date_match(match)
            if date:
                dated_lines.append((date, parse_time(line)))

    if not dated_lines:
        metadata_text = clean_text(metadata_date.get_text(' ', strip=True))
        matches = date_matches(metadata_text)
        match = matches[0] if matches else None
        if match:
            date = parse_date_match(match)
            if date:
                dated_lines.append((date, parse_time(metadata_text)))

    records = []
    for date, time_from in dict.fromkeys(dated_lines):
        records.append({
            'title': title,
            'date': date,
            'url': canonical_url(url),
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'description': description,
        })
    return records


def sitemap_urls():
    soup = BeautifulSoup(fetch(SITEMAP_URL).content, 'xml')
    # Archived Czech concert pages consistently use the "novinka-" path.  The
    # sitemap also contains many unrelated pages and English duplicates.
    urls = {
        canonical_url(node.get_text(strip=True))
        for node in soup.select('loc')
        if '/novinka-' in node.get_text(strip=True)
    }
    def index_links(index_url):
        soup = BeautifulSoup(fetch(index_url).text, 'html.parser')
        return [
            canonical_url(link.get('href'))
            for link in soup.select('main a[href]')
        ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        index_groups = executor.map(index_links, INDEX_URLS)
    for group in index_groups:
        for url in group:
            if urlsplit(url).netloc == 'www.kfpar.cz':
                urls.add(url)
    return sorted(urls)


def parse_tour_records():
    soup = BeautifulSoup(fetch(TOUR_URL).text, 'html.parser')
    bodies = soup.select('main article.cText')
    if not bodies:
        return []
    body = max(bodies, key=lambda node: len(node.get_text(' ', strip=True)))

    lines = [clean_text(line) for line in body.get_text('\n', strip=True).splitlines()]
    records = []
    for index, line in enumerate(lines):
        matches = date_matches(line)
        match = matches[0] if matches and matches[0].group(0) == line else None
        if not match or index + 1 >= len(lines):
            continue
        date = parse_date_match(match)
        location_line = lines[index + 1]
        city = infer_city(location_line)
        if not date or not city or not VENUE_WORDS.search(location_line):
            continue

        parts = [clean_text(part) for part in location_line.split('|')]
        venue = next((part for part in parts if VENUE_WORDS.search(part)), None)
        if not venue:
            continue
        description_line = lines[index + 2] if index + 2 < len(lines) else ''
        records.append({
            'title': parts[0],
            'date': date,
            'url': TOUR_URL,
            'time_from': None,
            'venue': venue,
            'city': city,
            'description': clean_text(f'{location_line}\n{description_line}'),
        })
    return records


def get_concerts():
    urls = sitemap_urls()
    log_message(
        'Concert candidates discovered from sitemap',
        event='crawler_urls_discovered',
        record_count=len(urls),
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        groups = executor.map(detail_records, urls)
        records = [record for group in groups for record in group]

    try:
        records.extend(parse_tour_records())
    except Exception as error:
        log_message(
            'Failed to parse touring schedule',
            event='crawler_item_failed',
            level='warning',
            url=TOUR_URL,
            error_type=type(error).__name__,
            error_message=str(error),
        )
    return records


class KfparCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='kfpar_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        upload_target='classical',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'venue',
            'city',
            'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        return get_concerts()


def main():
    KfparCrawler().run()


if __name__ == '__main__':
    main()
