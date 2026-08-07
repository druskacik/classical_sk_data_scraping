import re
from datetime import date
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.berliner-symphoniker.de/'
API_URL = f'{SOURCE_URL}wp-json/wp/v2/pages'
SOURCE = 'Berliner Symphoniker'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        'Chrome/125.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}

COUNTRY_NAMES = {
    'Deutschland': 'DE', 'Österreich': 'AT', 'Slowenien': 'SI',
    'Niederlande': 'NL', 'Portugal': 'PT', 'Japan': 'JP', 'Italien': 'IT',
    'Spanien': 'ES', 'Korea': 'KR', 'Vereinigte Arabische Emirate': 'AE',
}

# Places which occur in the published archive. Longer names are deliberately
# first so that Frankfurt is not selected from Frankfurt am Main, for example.
CITY_COUNTRIES = {
    'Frankfurt am Main': 'DE', 'Bad Kissingen': 'DE', 'La Chaise-Dieu': 'FR',
    'Chaise-Dieu': 'FR', 'Ljubljana': 'SI', 'Klagenfurt': 'AT', 'Amsterdam': 'NL',
    'Terneuzen': 'NL', 'Chemnitz': 'DE', 'Cottbus': 'DE', 'Potsdam': 'DE',
    'Salzburg': 'AT', 'Bebenhausen': 'DE', 'Sintra': 'PT', 'Lisboa': 'PT',
    'Tokushima': 'JP', 'Fukuoka': 'JP', 'Ichikawa': 'JP', 'Sapporo': 'JP',
    'Fukushima': 'JP', 'Yokohama': 'JP', 'Tokyo': 'JP', 'Morioka': 'JP',
    'Nagoya': 'JP', 'Osaka': 'JP', 'Kurashiki': 'JP', 'Chiba': 'JP',
    'Kure': 'JP', 'Hiroshima': 'JP', 'Shunan': 'JP', 'Okayama': 'JP',
    'Musashino': 'JP', 'Saitama': 'JP', 'Shizuoka': 'JP', 'Varenna': 'IT',
    'Sorrento': 'IT', 'Villa d’Agri': 'IT', 'Casal Velino': 'IT', 'Dubai': 'AE',
    'Gloggnitz': 'AT', 'Rheinsberg': 'DE', 'Neuruppin': 'DE', 'Stuttgart': 'DE',
    'Linz': 'AT', 'Rosenheim': 'DE', 'Wien': 'AT', 'Passau': 'DE',
    'München': 'DE', 'Innsbruck': 'AT', 'Leipzig': 'DE', 'Nürnberg': 'DE',
    'Bregenz': 'AT', 'Ulm': 'DE', 'Füssen': 'DE', 'Baden-Baden': 'DE',
    'Mannheim': 'DE', 'Frankfurt': 'DE', 'Zürich': 'CH', 'Hamburg': 'DE',
    'Bremen': 'DE', 'Hannover': 'DE', 'Dortmund': 'DE', 'Bayreuth': 'DE',
    'Verona': 'IT', 'Catanzano': 'IT', 'Würzburg': 'DE', 'Palma': 'ES',
    'Busan': 'KR', 'Geoje': 'KR', 'Seoul': 'KR', 'Chorin': 'DE',
    'Halle': 'DE', 'Berlin': 'DE',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_page(session, slug):
    response = session.get(
        API_URL,
        params={'slug': slug, '_fields': 'id,slug,link,title,content'},
        timeout=45,
    )
    response.raise_for_status()
    pages = response.json()
    return pages[0] if pages else None


def page_slug(url):
    if urlparse(url).netloc != urlparse(SOURCE_URL).netloc:
        return ''
    return urlparse(url).path.strip('/').split('/')[-1]


def event_sections(page):
    soup = BeautifulSoup(page['content']['rendered'], 'html.parser')
    for section in soup.select('section.elementor-top-section'):
        columns = section.select(':scope > .elementor-container > .elementor-column')
        if len(columns) >= 2 and re.search(
            r'\b\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*(?:\d{4}|\d{2})\b',
            clean_text(columns[0]),
        ):
            yield section, columns


def parse_dates(text):
    # Expand the site's common "27. und 28.12.24" notation. Date ranges and
    # tours are not expanded because they do not identify individual concerts.
    match = re.search(
        r'\b(?:(\d{1,2})\s*\.\s*(?:und|/)\s*)?'
        r'(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4}|\d{2})\b',
        text,
        re.I,
    )
    if not match:
        return []
    days = [match.group(2)]
    if match.group(1):
        days.insert(0, match.group(1))
    year = int(match.group(4))
    if year < 100:
        year += 2000
    results = []
    for day in days:
        try:
            results.append(date(year, int(match.group(3)), int(day)).isoformat())
        except ValueError:
            continue
    return results


def parse_times(text):
    prefix = re.split(r'\bUhr\b|\bh\b', text, maxsplit=1, flags=re.I)[0]
    date_match = re.search(r'\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*(?:\d{4}|\d{2})\b', prefix)
    if date_match:
        prefix = prefix[date_match.end():]
    values = re.findall(r'(?<!\d)([012]?\d)(?::([0-5]\d))?(?![\d.])', prefix)
    times = []
    for hour, minute in values:
        hour = int(hour)
        if hour < 24:
            value = f'{hour:02d}:{minute or "00"}'
            if value not in times:
                times.append(value)
    return times or [None]


def parse_location(text):
    location = text
    location = re.sub(
        r'^.*?\b\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*(?:\d{4}|\d{2})\b',
        '', location,
    )
    location = re.sub(r'^\s*[,|–-]?\s*(?:\d{1,2}(?::\d{2})?\s*(?:und\s*\d{1,2}(?::\d{2})?)?\s*(?:Uhr|h))?\s*[,|–-]?\s*', '', location, flags=re.I)
    location = clean_text(location).replace('\n', ' ').strip(' ,|–-')
    country_code = 'DE'
    for country_name, code in COUNTRY_NAMES.items():
        if country_name.casefold() in location.casefold():
            country_code = code
            break

    city = None
    for candidate, code in CITY_COUNTRIES.items():
        if re.search(rf'(?<!\w){re.escape(candidate)}(?!\w)', location, re.I):
            city, country_code = candidate, code
            break

    if not city:
        if re.search(r'\b(?:Philharmonie|Berliner Dom|UdK|Gendarmenmarkt|Adlon|Prenzlauer Berg|Hohenzollernplatz)\b', location, re.I):
            city = 'Berlin'
        elif re.search(r'\bKloster Bebenhausen\b', location, re.I):
            city = 'Bebenhausen'
        elif re.search(r'\bKloster Chorin\b', location, re.I):
            city = 'Chorin'
    if not city:
        return None

    venue = location
    venue = re.sub(rf'(?<!\w){re.escape(city)}(?!\w)', '', venue, flags=re.I)
    for country_name in COUNTRY_NAMES:
        venue = re.sub(rf'(?<!\w){re.escape(country_name)}(?!\w)', '', venue, flags=re.I)
    venue = re.sub(r'\s*[,|]\s*', ' ', venue)
    venue = re.sub(r'\s+', ' ', venue).strip(' ,|–-')
    if not venue and city == 'Ljubljana':
        # The linked Ljubljana Festival event identifies Cankarjev dom, while
        # the orchestra calendar abbreviates the location to city/country.
        venue = 'Cankarjev dom'
    if not venue:
        # Some listings name only a recognizable venue-city compound.
        original = clean_text(location)
        if original.casefold() != city.casefold():
            venue = original
    if not venue or venue.casefold() == city.casefold():
        return None
    return venue, city, country_code


def title_and_url(columns, page_url):
    strings = [clean_text(value) for value in columns[1].stripped_strings]
    strings = [value for value in strings if value]
    if not strings:
        return '', page_url
    title = strings[0]
    url = page_url
    for link in columns[1].select('a[href]'):
        if clean_text(link).casefold() == title.casefold():
            url = link['href']
            break
    if url == page_url:
        for column in columns[2:]:
            info = next((a.get('href') for a in column.select('a[href]') if clean_text(a).casefold() == 'info'), None)
            if info:
                url = info
                break
    return title, url


def detail_description(session, url, fallback):
    slug = page_slug(url)
    if not slug:
        return fallback or None
    try:
        page = get_page(session, slug)
    except (requests.RequestException, ValueError) as error:
        log_message(
            'Failed to fetch concert detail', event='crawler_item_failed', level='warning',
            url=url, error_type=type(error).__name__, error_message=str(error),
        )
        return fallback or None
    if not page:
        return fallback or None
    detail = clean_text(page['content']['rendered'])
    return detail or fallback or None


def records_from_page(session, page, fetch_details=False):
    records = []
    for section, columns in event_sections(page):
        date_location = clean_text(columns[0]).replace('\n', ' ')
        dates = parse_dates(date_location)
        times = parse_times(date_location)
        location = parse_location(date_location)
        if not location and len(columns) >= 3:
            location = parse_location(f'{date_location} {clean_text(columns[2])}')
        title, url = title_and_url(columns, page['link'])
        season = re.search(r'(20\d{2})-(\d{2})/?$', page_slug(page['link']))
        if season:
            season_start = int(season.group(1))
            season_years = {season_start, season_start + 1}
            corrected_dates = []
            for event_date in dates:
                event_year = int(event_date[:4])
                if event_year not in season_years:
                    # A few archive entries contain a dropped decade digit
                    # (2010 on the 2019/20 page). The month determines which
                    # side of the published season the event belongs to.
                    expected_year = season_start if int(event_date[5:7]) >= 8 else season_start + 1
                    event_date = f'{expected_year:04d}{event_date[4:]}'
                corrected_dates.append(event_date)
            dates = corrected_dates
        elif re.search(r'(?:-|/)2027/?$', url) and dates:
            # The current calendar places Passionskonzert beneath February
            # 2027 and links to passionskonzert-2027, but prints 2026.
            dates = [f'2027{event_date[4:]}' if event_date.startswith('2026-') else event_date for event_date in dates]
        if not dates or not location or not title or not url:
            continue
        venue, city, country_code = location
        fallback = clean_text(' '.join(clean_text(column) for column in columns[2:])) or None
        description = detail_description(session, url, fallback) if fetch_details else fallback
        for event_date in dates:
            for event_time in times:
                records.append({
                    'title': title, 'date': event_date, 'url': url,
                    'time_from': event_time, 'venue': venue, 'city': city,
                    'country_code': country_code, 'description': description,
                    'source_url': SOURCE_URL, 'source': SOURCE,
                })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    current = get_page(session, 'alle-konzerte')
    archive = get_page(session, 'konzertarchiv')
    if not current or not archive:
        raise RuntimeError('The concert calendar or archive page is unavailable')

    records = records_from_page(session, current, fetch_details=True)
    archive_soup = BeautifulSoup(archive['content']['rendered'], 'html.parser')
    archive_slugs = []
    for link in archive_soup.select('a[href]'):
        slug = page_slug(link['href'])
        if 'spielzeit' in slug and slug not in archive_slugs:
            archive_slugs.append(slug)
    for slug in archive_slugs:
        try:
            page = get_page(session, slug)
            if page:
                records.extend(records_from_page(session, page))
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to scrape archive page', event='crawler_item_failed', level='warning',
                url=f'{SOURCE_URL}{slug}/', error_type=type(error).__name__,
                error_message=str(error),
            )

    unique = {}
    for record in records:
        key = (record['title'], record['date'], record['time_from'], record['venue'])
        unique[key] = record
    return sorted(unique.values(), key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class BerlinerSymphonikerDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='berliner_symphoniker_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    BerlinerSymphonikerDeCrawler().run()


if __name__ == '__main__':
    main()
