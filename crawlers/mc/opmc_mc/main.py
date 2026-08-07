import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://opmc.mc/'
CATALOG_URL = f'{SOURCE_URL}nos-precedentes-saisons/'
SOURCE = 'Orchestre Philharmonique de Monte-Carlo'
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.7',
}
MONTHS = {
    'jan': 1, 'feb': 2, 'fev': 2, 'mar': 3, 'apr': 4, 'avr': 4,
    'may': 5, 'mai': 5, 'jun': 6, 'juin': 6, 'jul': 7, 'juil': 7,
    'aug': 8, 'aout': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(item):
    month_node = item.select_one('.ova_thumbnail .time .month')
    year_node = item.select_one('.ova_thumbnail .time .date')
    match = re.fullmatch(r'(\d{1,2})\s+([^\W\d_]+)', clean_text(month_node), re.UNICODE)
    if not match or not re.fullmatch(r'\d{4}', clean_text(year_node)):
        return None
    month_name = unicodedata.normalize('NFKD', match.group(2).lower())
    month_name = ''.join(char for char in month_name if not unicodedata.combining(char))
    month = MONTHS.get(month_name)
    if not month:
        return None
    try:
        return date(int(clean_text(year_node)), month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = re.search(r'(?<!\d)(\d{1,2})\s*h\s*(\d{2})(?!\d)', clean_text(value), re.I)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def resolve_location(value):
    value = clean_text(value)
    if not value:
        return None
    # Venue strings in this catalogue are a small, stable inventory. Explicit
    # touring locations take precedence over the orchestra's Monaco base.
    locations = (
        (('monaco', 'monte-carlo'), 'Monaco', 'MC'),
        (('salzbourg',), 'Salzburg', 'AT'),
        (('granada',), 'Granada', 'ES'),
        (('bucarest',), 'Bucharest', 'RO'),
        (('genève',), 'Geneva', 'CH'),
        (('bâle',), 'Basel', 'CH'),
        (('zurich',), 'Zurich', 'CH'),
        (('amsterdam',), 'Amsterdam', 'NL'),
        (('fiorentino',), 'Florence', 'IT'),
        (('aix-en-provence',), 'Aix-en-Provence', 'FR'),
        (('orange',), 'Orange', 'FR'),
        (('brest',), 'Brest', 'FR'),
        (('paris',), 'Paris', 'FR'),
        (('evian',), 'Evian-les-Bains', 'FR'),
        (("roque d'anthéron", "r. d'anthéron"), 'La Roque-d’Anthéron', 'FR'),
        (('vannes',), 'Vannes', 'FR'),
        (('versailles',), 'Versailles', 'FR'),
        (('beaulieu',), 'Beaulieu-sur-Mer', 'FR'),
    )
    lowered = value.lower()
    for markers, city, country_code in locations:
        if any(marker in lowered for marker in markers):
            venue = re.sub(r'\s*,\s*(?:Monaco|Salzbourg \(Autriche\)|Granada \(Espagne\)|'
                           r'Bucarest \(Roumanie\)|Genève \(Suisse\)|Bâle \(Suisse\)|'
                           r'Zurich \(Suisse\))\s*$', '', value, flags=re.I)
            return venue, city, country_code
    return None


def parse_catalog(html):
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for item in soup.select('.ovaem_events_filter_content .ova-item'):
        title_link = item.select_one('.wrap_content h2.title a[href*="/concert/"]')
        venue_node = item.select_one('.wrap_content .venue:not(.event-hour)')
        location = resolve_location(venue_node)
        event_date = parse_date(item)
        title = clean_text(title_link)
        url = title_link.get('href', '').strip() if title_link else ''
        if not title or not url or not event_date or not location:
            continue
        venue, city, country_code = location
        excerpt = clean_text(item.select_one('.wrap_content .except')) or None
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': parse_time(item.select_one('.wrap_content .event-hour')),
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': excerpt,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def parse_detail_description(html):
    soup = BeautifulSoup(html, 'html.parser')
    description = soup.select_one('.content .desc')
    if not description:
        return None
    for node in description.select('script, style, noscript, .tarifs_concerts'):
        node.decompose()
    return clean_text(description) or None


def fetch_html(url):
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    return response.text


def enrich_record(record):
    try:
        description = parse_detail_description(fetch_html(record['url']))
        if description:
            record['description'] = description
    except requests.RequestException as error:
        log_message(
            'Failed to fetch OPMC concert detail; using catalogue description',
            event='crawler_item_failed',
            level='warning',
            url=record['url'],
            error_type=type(error).__name__,
            error_message=str(error),
        )
    return record


def get_concerts():
    records = parse_catalog(fetch_html(CATALOG_URL))
    enriched = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(enrich_record, record) for record in records]
        for future in as_completed(futures):
            enriched.append(future.result())
    return sorted(enriched, key=lambda item: (
        item['date'], item['time_from'] or '', item['title'], item['url']
    ))


class OpmcMcCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='opmc_mc',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='MC',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['url', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    OpmcMcCrawler().run()


if __name__ == '__main__':
    main()
