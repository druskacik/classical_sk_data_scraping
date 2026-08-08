import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.bruckner-orchester.at/'
CALENDAR_URL = urljoin(SOURCE_URL, 'kalender')
ARCHIVE_URL = urljoin(SOURCE_URL, 'konzertarchiv')
SOURCE = 'Bruckner Orchester Linz'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

DATE_TIME_RE = re.compile(r'(\d{2}\.\d{2}\.\d{4})(?:\s*-?\s*(\d{2}:\d{2}))?')
TEASER_PREFIX_RE = re.compile(
    r'^(?:Mo|Di|Mi|Do|Fr|Sa|So)\s+\d{1,2}\.\d{1,2}\.\s+\d{1,2}[.:]\d{2}\s*',
    re.IGNORECASE,
)


def clean_text(value, separator=' '):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text(separator, strip=True)
    else:
        text = str(value)
        if '<' in text and '>' in text:
            text = BeautifulSoup(text, 'html.parser').get_text(separator, strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    return re.sub(r'\s+', ' ', text).strip()


def text_lines(element):
    if not element:
        return []
    return [clean_text(line) for line in element.get_text('\n').splitlines() if clean_text(line)]


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def property_values(soup):
    values = {}
    for row in soup.select('.eb-event-property'):
        label = clean_text(row.select_one('.eb-event-property-label'))
        value = clean_text(row.select_one('.eb-event-property-value'))
        if label and value:
            values[label] = value
    return values


def listing_items(soup):
    items = {}
    selectors = ('.eb-event-container', '.eb-event-item-grid-default-layout')
    for block in soup.select(', '.join(selectors)):
        link = block.select_one('.eb-event-title[href]')
        if not link:
            continue
        url = urljoin(SOURCE_URL, link['href'])
        date_node = block.select_one('.eb-event-date-info, .eb-event-date-time')
        date_text = clean_text(date_node)
        match = DATE_TIME_RE.search(date_text)
        location = clean_text(block.select_one('.eb-event-location'))
        if not location:
            info = block.select_one('.eb-event-information')
            if info:
                marker = info.select_one('.fa-map-marker')
                if marker and marker.parent:
                    location = clean_text(marker.parent)
        description = block.select_one('.eb-description-details')
        items[url] = {
            'url': url,
            'title': clean_text(link),
            'date_text': match.group(1) if match else '',
            'time': match.group(2) if match else None,
            'location': location,
            'teaser_lines': text_lines(description),
        }
    return items


def venue_from_lines(lines):
    for line in lines:
        candidate = TEASER_PREFIX_RE.sub('', line).strip(' ,-')
        candidate = re.sub(r'^[.&\s]*(?:\d{1,2}[.:]\d{2}\s*)?', '', candidate)
        if candidate != line and re.search(
            r'(?:haus|saal|foyer|lounge|dom|kirche|basilika|stift|park|theater|museum|'
            r'zentrum|arena|schloss|verein)\b|\bhof\b',
            candidate,
            re.IGNORECASE,
        ) and not re.search(r'\bKarten|Ticket|Information', candidate, re.IGNORECASE):
            return candidate

    venue_patterns = (
        r'(?:Gro(?:ß|ss)er Saal|Foyer)\s+Musiktheater',
        r'BlackBox(?:\s+Lounge)?',
        r'Brucknerhaus(?:\s+Linz)?',
        r'Musikverein(?:\s+Wien)?',
        r'Mariendom(?:\s+Linz)?',
        r'Prinzregententheater',
        r'Konzerthaus\s+Blaibach',
        r'Toscanapark',
        r'(?:Basilika|Stiftskirche)\s+(?:St\.?\s*)?Florian',
    )
    for line in lines:
        if len(line) > 120:
            continue
        for pattern in venue_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                return match.group(0)
    return None


def resolve_location(location, title, description_lines, teaser_lines, categories):
    haystack = ' '.join([title, location, categories, *teaser_lines, *description_lines])
    venue = venue_from_lines(teaser_lines) or venue_from_lines(description_lines)

    if re.search(r'Passau|Dom St\.? Stephan', haystack, re.IGNORECASE):
        return venue or 'Dom St. Stephan', 'Passau', 'DE'
    if re.search(r'Blaibach|Konzerthaus Blaibach', haystack, re.IGNORECASE):
        return venue or 'Konzerthaus Blaibach', 'Blaibach', 'DE'
    if re.search(r'München|Prinzregententheater', haystack, re.IGNORECASE):
        return venue or 'Prinzregententheater', 'München', 'DE'
    if re.search(r'Toscanapark', haystack, re.IGNORECASE):
        return venue or 'Toscanapark', 'Gmunden', 'AT'
    if re.search(r'Gmunden', haystack, re.IGNORECASE):
        return venue, 'Gmunden', 'AT'
    if re.search(r'(?:St\.?\s*)Florian|Stiftskirche|Basilika', haystack, re.IGNORECASE):
        return venue or 'Basilika St. Florian', 'Sankt Florian', 'AT'
    if re.search(r'Steinbach(?: am Attersee)?', haystack, re.IGNORECASE):
        return venue, 'Steinbach am Attersee', 'AT'
    if re.search(r'Reichersberg', haystack, re.IGNORECASE):
        return venue or 'Stift Reichersberg', 'Reichersberg', 'AT'
    if re.search(r'\bWels\b', haystack, re.IGNORECASE):
        return venue, 'Wels', 'AT'
    if location.casefold() == 'graz' or re.search(r'\bGraz\b', title, re.IGNORECASE):
        return venue, 'Graz', 'AT'
    if location.casefold() == 'wien' or re.search(r'\bWien\b', title, re.IGNORECASE):
        return venue, 'Wien', 'AT'
    if location.casefold() in {'linz', 'musiktheater'}:
        if not venue and 'bol zyklus im brucknerhaus' in categories.casefold():
            venue = 'Brucknerhaus Linz'
        if not venue and ('musiktheater' in location.casefold() or 'musiktheater' in categories.casefold()):
            venue = 'Musiktheater Linz'
        return venue, 'Linz', 'AT'
    return None, None, None


def make_record(item, soup):
    title = clean_text(soup.select_one('.eb-page-heading')) or item['title']
    properties = property_values(soup)
    start = properties.get('Beginn der Veranstaltung', '')
    match = DATE_TIME_RE.search(start)
    date_text = match.group(1) if match else item['date_text']
    time_from = match.group(2) if match else item['time']
    try:
        event_date = datetime.strptime(date_text, '%d.%m.%Y').date().isoformat()
    except (TypeError, ValueError):
        return None

    description_node = soup.select_one('#eb-event-details .eb-description-details')
    description_lines = text_lines(description_node)
    description = clean_text(description_node, separator='\n') or None
    location = properties.get('Veranstaltungsort') or item['location']
    categories = properties.get('Veranstaltungskategorien', '')
    venue, city, country_code = resolve_location(
        location, title, description_lines, item['teaser_lines'], categories
    )
    if not title or not venue or not city or not country_code:
        return None

    return {
        'title': title,
        'date': event_date,
        'url': item['url'],
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': country_code,
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = {}
    for listing_url in (CALENDAR_URL, ARCHIVE_URL):
        items.update(listing_items(get_soup(session, listing_url)))

    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(get_soup, session, url): item for url, item in items.items()}
        for future in as_completed(futures):
            item = futures[future]
            try:
                record = make_record(item, future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Bruckner Orchester concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            if record:
                records.append(record)

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class BrucknerOrchesterAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bruckner_orchester_at',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AT',
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
    BrucknerOrchesterAtCrawler().run()


if __name__ == '__main__':
    main()
