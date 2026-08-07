import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.nch.ie/'
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')
SOURCE = 'National Concert Hall'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-IE,en;q=0.9',
}

MONTHS = {
    name: number
    for number, name in enumerate(
        ('January', 'February', 'March', 'April', 'May', 'June',
         'July', 'August', 'September', 'October', 'November', 'December'),
        1,
    )
}

# These are used only when an event explicitly names a location away from the
# NCH. Events without such a signal are at the venue's Dublin campus.
IRISH_CITIES = (
    'Athlone', 'Belfast', 'Bray', 'Cork', 'Derry', 'Drogheda', 'Dublin',
    'Dundalk', 'Galway', 'Kilkenny', 'Killarney', 'Letterkenny', 'Limerick',
    'Sligo', 'Tralee', 'Waterford', 'Wexford',
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_page(url):
    last_error = None
    for attempt in range(4):
        try:
            response = requests.get(url, headers=HEADERS, timeout=45)
            response.raise_for_status()
            return response.text
        except requests.RequestException as error:
            last_error = error
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
    raise last_error


def event_urls():
    soup = BeautifulSoup(get_page(SITEMAP_URL), 'xml')
    urls = []
    for location in soup.find_all('loc'):
        url = location.get_text(strip=True)
        path = url.rstrip('/')
        if '/all-events-listing/' in url and not path.endswith('/all-events-listing'):
            urls.append(url)
    return list(dict.fromkeys(urls))


def parse_date(value):
    match = re.search(
        r'\b(\d{1,2})(?:st|nd|rd|th)?\s+'
        r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
        r'\s+(\d{4})\b',
        value,
        re.I,
    )
    if not match:
        return None
    month_name = match.group(2).lower()
    month = next(number for name, number in MONTHS.items() if name.lower().startswith(month_name[:3]))
    try:
        return date(int(match.group(3)), month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*([AP]M)\b', value, re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).upper() == 'PM':
        hour += 12
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def labelled_values(soup):
    values = {}
    for term in soup.select('main dt'):
        definition = term.find_next_sibling('dd')
        if definition:
            values.setdefault(clean_text(term.get_text()), clean_text(definition.get_text(' ')))
    return values


def resolve_city(title, venue):
    location_text = f'{venue} {title}'
    for city in IRISH_CITIES:
        if re.search(rf'\b{re.escape(city)}\b', location_text, re.I):
            return city
    # Venue labels such as Main Stage, The Studio, and Kevin Barry Room are
    # rooms on the National Concert Hall's Dublin campus.
    return 'Dublin'


def parse_event(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    main = soup.select_one('main')
    title_node = main.select_one('h1') if main else None
    title = clean_text(title_node.get_text(' ')) if title_node else ''
    values = labelled_values(soup)
    event_date = parse_date(values.get('Date', ''))
    venue = clean_text(values.get('Venue'))
    if not title or not event_date or not venue or venue.lower() in {'online', 'n/a'}:
        return None

    descriptions = []
    for block in soup.select('main .wysiwyg'):
        text = clean_text(block)
        if text and text not in descriptions:
            descriptions.append(text)

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': parse_time(values.get('Time', '')),
        'venue': venue,
        'city': resolve_city(title, venue),
        'country_code': 'IE',
        'description': '\n\n'.join(descriptions) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    records = []
    # The site rate-limits larger bursts quite aggressively.
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(get_page, url): url for url in event_urls()}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = parse_event(url, future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda item: (item['date'], item['time_from'] or '', item['title'], item['url']),
    )


class NchIeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='nch_ie',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='IE',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from'],
    )

    def scrape(self):
        return get_concerts()


def main():
    NchIeCrawler().run()


if __name__ == '__main__':
    main()
