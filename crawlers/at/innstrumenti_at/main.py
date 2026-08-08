import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://innstrumenti.at/'
API_URL = f'{SOURCE_URL}wp-json/wp/v2/konzert'
SOURCE = 'Tiroler Kammerorchester InnStrumenti'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'jan': 1, 'jän': 1, 'januar': 1, 'feb': 2, 'februar': 2,
    'mär': 3, 'märz': 3, 'apr': 4, 'april': 4, 'mai': 5,
    'jun': 6, 'juni': 6, 'jul': 7, 'juli': 7, 'aug': 8,
    'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
    'okt': 10, 'oktober': 10, 'nov': 11, 'november': 11,
    'dez': 12, 'dezember': 12,
}

ITALIAN_CITIES = {'Bozen', 'Brixen', 'Meran'}


def clean_text(value):
    if not value:
        return ''
    raw = str(value)
    text = (
        BeautifulSoup(raw, 'html.parser').get_text('\n', strip=True)
        if '<' in raw
        else raw.strip()
    )
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_datetime(value):
    match = re.search(
        r'(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\.?\s*(20\d{2})'
        r'(?:\s*\([^)]*\))?\s*,\s*(\d{1,2})[.:](\d{2})',
        value,
    )
    if not match:
        return None, None
    month = MONTHS.get(match.group(2).lower().rstrip('.'))
    if not month:
        return None, None
    try:
        event_date = date(
            int(match.group(3)), month, int(match.group(1))
        ).isoformat()
    except ValueError:
        return None, None
    return event_date, f'{int(match.group(4)):02d}:{match.group(5)}'


def clean_city(value):
    return re.sub(r'\s*\([^)]*\)\s*$', '', clean_text(value)).strip()


def clean_venue(value):
    venue = clean_text(value).lstrip('|').strip()
    if re.search(r'wetterbedingt.*haus der musik', venue, re.I):
        return 'Haus der Musik'
    venue = re.sub(r'\s*\([^)]*(?:wetter|schlechtwetter)[^)]*\)', '', venue, flags=re.I)
    venue = re.sub(r'\s*/\s*Bahnhofstra(?:ss|ß)e\s+\d+.*$', '', venue, flags=re.I)
    return venue.strip(' ,-')


def event_datetime(soup):
    for element in soup.select('.elementor-shortcode'):
        parsed = parse_datetime(clean_text(element))
        if parsed[0]:
            return element, parsed
    return None, (None, None)


def event_location(date_element):
    container = date_element.find_parent(class_='e-con-inner')
    info = container.select_one('.elementor-widget-post-info') if container else None
    if not info:
        return '', ''
    parts = [clean_text(item).lstrip('|').strip() for item in info.select('li')]
    parts = [part for part in parts if part]
    if len(parts) >= 2:
        return clean_city(parts[0]), clean_venue(parts[1])
    if len(parts) == 1 and 'Wiltener Basilika' in parts[0]:
        return 'Innsbruck', 'Wiltener Basilika'
    return '', ''


def event_description(soup):
    parts = []
    for element in soup.select('main .elementor-widget-text-editor'):
        text = clean_text(element)
        if len(text) >= 40 and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def parse_event(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    heading = soup.select_one('main h1')
    title = clean_text(heading)
    date_element, (event_date, time_from) = event_datetime(soup)
    city, venue = event_location(date_element) if date_element else ('', '')
    country_code = 'IT' if city in ITALIAN_CITIES else 'AT'
    if not title or not event_date or not venue or not city:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': event_description(soup),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def fetch_event(event):
    url = clean_text(event.get('link'))
    if not url:
        return None
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    return parse_event(response.text, url)


class InnstrumentiAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='innstrumenti_at',
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
        response = requests.get(
            API_URL,
            params={'per_page': 100, 'orderby': 'date', 'order': 'asc', '_fields': 'link,title'},
            headers=HEADERS,
            timeout=45,
        )
        response.raise_for_status()
        events = response.json()
        records = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_event, event): event for event in events}
            for future in as_completed(futures):
                event = futures[future]
                url = clean_text(event.get('link'))
                try:
                    record = future.result()
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape InnStrumenti concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                    continue
                if record:
                    records.append(record)
                else:
                    log_message(
                        'Skipped incomplete InnStrumenti concert',
                        event='crawler_item_skipped',
                        level='warning',
                        url=url,
                        error_type='IncompleteEventData',
                        error_message='Required date, title, URL, venue, or city is missing',
                    )
        return sorted(
            records,
            key=lambda item: (
                item['date'], item['time_from'] or '', item['title'], item['venue']
            ),
        )


def main():
    InnstrumentiAtCrawler().run()


if __name__ == '__main__':
    main()
