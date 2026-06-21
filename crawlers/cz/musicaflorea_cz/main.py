import re
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.musicaflorea.cz'
CONCERTS_URL = f'{BASE_URL}/koncerty/'
PAGE_API_URL = f'{BASE_URL}/wp-json/wp/v2/pages'
SOURCE = 'Musica Florea'
SOURCE_URL = BASE_URL

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

KNOWN_CITIES = [
    'České Budějovice',
    'Karlovy Vary',
    'Lanškroun',
    'Litomyšl',
    'Nymburk',
    'Olomouc',
    'Praha',
    'Regensburg',
    'Teplice',
    'Třebíč',
    'Valtice',
    'Brno',
    'Kuks',
    'Nové Hrady',
]


def clean_text(text):
    if not text:
        return ''
    text = unescape(str(text)).replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def clean_url(url):
    if not url:
        return None

    parsed = urlsplit(url)
    kept_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(('utm_', '_gl', '_ga', 'fbclid', 'gclid'))
    ]
    cleaned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(kept_params), ''))
    if len(cleaned) > 255:
        cleaned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
    return cleaned


def normalize_date_text(date_text):
    return re.sub(r'\b(\d)\s+(\d\.)', r'\1\2', clean_text(date_text))


def get_soup(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def fetch_concerts_soup(session):
    response = session.get(
        PAGE_API_URL,
        params={'slug': 'koncerty', '_fields': 'link,title,content'},
        timeout=30,
    )
    response.raise_for_status()
    pages = response.json()
    if pages:
        return BeautifulSoup(pages[0].get('content', {}).get('rendered', ''), 'html.parser')

    page = get_soup(session, CONCERTS_URL)
    main = page.select_one('main') or page
    return main


def parse_date(date_text):
    date_text = normalize_date_text(date_text)
    year_match = re.search(r'\b(20\d{2})\b', date_text)
    date_match = re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.', date_text)
    if not year_match or not date_match:
        return None

    day = int(date_match.group(1))
    month = int(date_match.group(2))
    year = int(year_match.group(1))
    return f'{year}-{month:02d}-{day:02d}'


def parse_time(date_text):
    match = re.search(r'\b(\d{1,2}):(\d{2})\b', normalize_date_text(date_text))
    if not match:
        return None
    return f'{int(match.group(1)):02d}:{match.group(2)}'


def normalize_city(city):
    city = clean_text(city)
    if not city:
        return None

    aliases = {
        'CESKE BUDEJOVICE': 'České Budějovice',
        'ČESKÉ BUDĚJOVICE': 'České Budějovice',
        'KARLOVY VARY': 'Karlovy Vary',
        'LANSKROUN': 'Lanškroun',
        'LANŠKROUN': 'Lanškroun',
        'LITOMYSL': 'Litomyšl',
        'LITOMYŠL': 'Litomyšl',
        'NYMBURK': 'Nymburk',
        'OLOMOUC': 'Olomouc',
        'REGENSBURG': 'Regensburg',
        'TEPLICE': 'Teplice',
        'TREBIC': 'Třebíč',
        'TŘEBÍČ': 'Třebíč',
        'VALTICE': 'Valtice',
        'BRNO': 'Brno',
        'KUKS': 'Kuks',
        'NOVE HRADY': 'Nové Hrady',
        'NOVÉ HRADY': 'Nové Hrady',
    }

    if re.match(r'^PRAHA(?:\s+\d+)?$', city, re.IGNORECASE):
        return 'Praha'

    return aliases.get(city.upper(), city)


def infer_city(location):
    location = clean_text(location)
    leading = re.match(r'^([A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]+(?:\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]+)*)(?:\s+\d+)?\b', location)
    if leading:
        city = normalize_city(leading.group(1))
        if city in KNOWN_CITIES:
            return city

    for city in KNOWN_CITIES:
        if re.search(rf'\b{re.escape(city)}\b', location, re.IGNORECASE):
            return city

    return None


def extract_venue(location, city):
    location = clean_text(location).strip(' ,')
    if not location:
        return None
    if not city:
        return location

    venue = re.sub(rf'^{re.escape(city)}(?:\s+\d+)?\s*', '', location, flags=re.IGNORECASE)

    if city == 'Praha':
        venue = re.sub(r'^PRAHA(?:\s+\d+)?\s*', '', venue, flags=re.IGNORECASE)
    elif city == 'Brno':
        venue = re.sub(r'^BRNO\s*', '', venue, flags=re.IGNORECASE)
    elif city == 'Nymburk':
        venue = re.sub(r'^NYMBURK\s*', '', venue, flags=re.IGNORECASE)
    elif city == 'Valtice':
        venue = re.sub(r'^VALTICE\s*', '', venue, flags=re.IGNORECASE)
    elif city == 'Teplice':
        venue = re.sub(r'^TEPLICE\s*', '', venue, flags=re.IGNORECASE)
    elif city == 'Karlovy Vary':
        venue = re.sub(r'^KARLOVY VARY\s*', '', venue, flags=re.IGNORECASE)
    elif city == 'České Budějovice':
        venue = re.sub(r'^ČESKÉ BUDĚJOVICE\s*', '', venue, flags=re.IGNORECASE)
    elif city == 'Třebíč':
        venue = re.sub(r'^TŘEBÍČ\s*', '', venue, flags=re.IGNORECASE)

    replacements = {
        'Tage alter Musik Regensburg': 'Tage alter Musik',
        'BAROKO 2026 Vlastivědné muzeum v Olomouci, Sál Václava III.': (
            'Vlastivědné muzeum v Olomouci, Sál Václava III.'
        ),
        'Smetanova Litomyšl Smetanův dům': 'Smetanův dům',
        'Smetanova Litomyšl Večer na zámku v Nových Hradech': 'Večer na zámku v Nových Hradech',
        'FreshDance fest areál u Zámku Lanškroun': 'areál u Zámku Lanškroun',
        'Theatrum Kuks nádvoří hospitalu': 'nádvoří hospitalu',
    }

    venue = replacements.get(location, venue)
    return clean_text(venue.strip(' ,')) or location


def section_heading_for_table(table):
    for previous in table.find_all_previous(['h1', 'h2', 'h3']):
        heading = clean_text(previous.get_text(' ', strip=True))
        if heading:
            return heading
    return None


def row_cells(row):
    return [clean_text(cell.get_text(' ', strip=True)) for cell in row.select('td')]


def extract_concert(row, section_heading):
    cells = row_cells(row)
    if len(cells) < 3:
        return None

    date_text, title, location = cells[:3]
    date_text = normalize_date_text(date_text)
    date = parse_date(date_text)
    if not date or not title:
        return None

    link = row.select_one('a[href]')
    url = clean_url(urljoin(CONCERTS_URL, link.get('href'))) if link else CONCERTS_URL
    city = infer_city(location)
    venue = extract_venue(location, city)

    description_parts = [
        section_heading,
        f'Název: {title}',
        f'Datum a čas: {date_text}',
        f'Místo: {location}',
    ]
    if link:
        description_parts.append(f'Vstupenky/detail: {url}')

    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': parse_time(date_text),
        'time_to': None,
        'venue': venue,
        'city': city,
        'description': clean_text('\n'.join(part for part in description_parts if part)),
        'type': 'concert',
    }


def extract_concerts(soup):
    concerts = []
    for table in soup.select('table'):
        section_heading = section_heading_for_table(table)
        for row in table.select('tr'):
            concert = extract_concert(row, section_heading)
            if concert:
                concerts.append(concert)
    return concerts


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    return extract_concerts(fetch_concerts_soup(session))


class MusicaFloreaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='musicaflorea_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'time_to',
            'venue',
            'city',
            'description',
            'type',
        ],
        dedupe_subset=['title', 'date', 'url'],
        front_fields=[
            ('source_url', SOURCE_URL),
            ('source', SOURCE),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    MusicaFloreaCrawler().run()


if __name__ == '__main__':
    main()
