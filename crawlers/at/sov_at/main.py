import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.sov.at/'
CALENDAR_URL = urljoin(SOURCE_URL, 'konzerte/kalender')
ARCHIVE_URL = urljoin(SOURCE_URL, 'konzerte/archiv')
SOURCE = 'SOV Symphonieorchester Vorarlberg'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

# The listings provide venue names but no separate city field. These are the
# venue labels present in the site's current calendar and published archive.
VENUE_CITIES = {
    'Bahnhof Schwarzenberg': 'Schwarzenberg',
    'Bludenz Remise': 'Bludenz',
    'Erlöserkirche Rheindorf, Lustenau': 'Lustenau',
    'Festspielhaus (Generalprobe)': 'Bregenz',
    'Festspielhaus Bregenz': 'Bregenz',
    'Festspielhaus Salzburg': 'Salzburg',
    'Fontanella Seewaldsee': 'Fontanella',
    'Klosterkirche Mehrerau': 'Bregenz',
    'Konzerthaus Wien': 'Wien',
    'Kulturbühne AMBACH Götzis': 'Götzis',
    'Kunsthaus Bregenz': 'Bregenz',
    'Lechwelten, Lech am Arlberg': 'Lech am Arlberg',
    'Marktplatz Bregenz': 'Bregenz',
    'Marktplatz Dornbirn': 'Dornbirn',
    'Mellau Pfarrkirche': 'Mellau',
    'Montforthaus Feldkirch': 'Feldkirch',
    'ORF Landesstudio Dornbirn': 'Dornbirn',
    'ORF Radio Vorarlberg': 'Dornbirn',
    'Pfarrkirche Herz Jesu, Bregenz': 'Bregenz',
    'Pfarrkirche St. Gallus, Bregenz': 'Bregenz',
    'Pfarrkirche St. Karl, Hohenems': 'Hohenems',
    'Schruns Kulturbühne': 'Schruns',
    'Seebühne Bregenz': 'Bregenz',
    'St. Gebhard Bregenz': 'Bregenz',
    'Theater am Kornmarkt': 'Bregenz',
    'Vorarlberger Landestheater': 'Bregenz',
    'Werkstattbühne Bregenz': 'Bregenz',
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_html(url):
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return response.text


def listing_rows(html):
    soup = BeautifulSoup(html, 'html.parser')
    return soup.select(
        '.view-konzerte-alle-termine .views-row, '
        '.view-konzerte-archiv .views-row'
    )


def parse_listing_row(row):
    title_link = row.select_one('.views-field-title a[href]')
    date_element = row.select_one('.views-field-field-datum time[datetime]')
    time_element = row.select_one('.views-field-field-datum-1 time[datetime]')
    venue_element = row.select_one('.views-field-field-veranstaltungsort')

    title = clean_text(title_link)
    url = urljoin(SOURCE_URL, title_link.get('href', '')) if title_link else ''
    venue = clean_text(venue_element)
    city = VENUE_CITIES.get(venue)
    raw_date = date_element.get('datetime', '')[:10] if date_element else ''
    raw_time = time_element.get('datetime', '')[11:16] if time_element else ''

    if 'abgesagt' in clean_text(row).lower():
        return None
    try:
        event_date = date.fromisoformat(raw_date).isoformat()
    except ValueError:
        return None
    if not title or not url or not venue or not city:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': raw_time or None,
        'venue': venue,
        'city': city,
        'country_code': 'AT',
        'description': None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def parse_description(html):
    soup = BeautifulSoup(html, 'html.parser')
    parts = []
    body = clean_text(soup.select_one('.field--name-body'))
    if body:
        parts.append(body)

    programme = clean_text(soup.select_one('.field--name-field-programm'))
    if programme:
        parts.append('Programm\n' + programme)

    return '\n\n'.join(parts) or None


def scrape_concerts():
    records_by_key = {}
    for listing_url in (CALENDAR_URL, ARCHIVE_URL):
        html = fetch_html(listing_url)
        for row in listing_rows(html):
            record = parse_listing_row(row)
            if not record:
                continue
            key = (
                record['title'], record['date'], record['time_from'],
                record['venue'], record['city'],
            )
            records_by_key[key] = record

    records = list(records_by_key.values())
    descriptions = {}
    urls = sorted({record['url'] for record in records})
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_html, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                descriptions[url] = parse_description(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape SOV concert detail',
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
        key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['venue']
        ),
    )


class SovAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='sov_at',
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
        return scrape_concerts()


def main():
    SovAtCrawler().run()


if __name__ == '__main__':
    main()
