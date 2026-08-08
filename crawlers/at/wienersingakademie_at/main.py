import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.wienersingakademie.at/de/startseite/'
EVENTS_URL = 'https://www.wienersingakademie.at/de/termine/'
SOURCE = 'Wiener Singakademie'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-AT,de;q=0.9,en;q=0.7',
}

MONTHS = {
    'januar': 1, 'februar': 2, 'märz': 3, 'april': 4, 'mai': 5,
    'juni': 6, 'juli': 7, 'august': 8, 'september': 9, 'oktober': 10,
    'november': 11, 'dezember': 12,
}

PERFORMANCE_RE = re.compile(
    r'(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),\s*'
    r'(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\s+(\d{4})\s*'
    r'\((?:ab\s+)?(\d{1,2}[:.]\d{2}(?:\s*&\s*\d{1,2}[:.]\d{2})?)'
    r'(?:\s*(?:Uhr|p\.m\.))?\)\s*\|\s*'
    r'(.+?)(?=(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),'
    r'\s*\d{1,2}\.|$)'
)


def clean_text(value):
    if not value:
        return ''
    text = value.get_text(' ', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    return re.sub(r'\s+', ' ', text).strip()


def event_blocks(content):
    """Yield the elements belonging to each programme entry."""
    block = []
    for element in content.children:
        if not getattr(element, 'name', None):
            continue
        if element.name == 'h2':
            block = []
            continue
        if element.name == 'hr':
            if block:
                yield block
            block = []
            continue
        if element.name in {'h3', 'h4', 'p'} and clean_text(element):
            block.append(element)
    if block:
        yield block


def resolve_location(location):
    location = re.sub(
        r'\s+Die Aufführung kann im Livestream.*$', '', clean_text(location), flags=re.I
    )
    rules = (
        (r'^Athen,\s*(.+)$', 'Athen', 'GR'),
        (r'^Bruck a\.d\. Leitha,\s*(.+)$', 'Bruck an der Leitha', 'AT'),
        (r'^Eisenstadt,\s*(.+)$', 'Eisenstadt', 'AT'),
        (r'^Elbphilharmonie Hamburg', 'Hamburg', 'DE'),
        (r'^Festspielhaus Baden-Baden$', 'Baden-Baden', 'DE'),
        (r'^Festspielhaus Bregenz$', 'Bregenz', 'AT'),
        (r'^Konzerthalle Bamberg$', 'Bamberg', 'DE'),
        (r'^Landestheater Linz', 'Linz', 'AT'),
        (r'^Montforthaus Feldkirch$', 'Feldkirch', 'AT'),
        (r'^Philharmonie Luxemburg', 'Luxemburg', 'LU'),
        (r'^Pfarre St\. Othmar, Mödling$', 'Mödling', 'AT'),
        (r'^(?:Schloss Esterházy, Eisenstadt|Schloss Esterházy, Haydnsaal)$', 'Eisenstadt', 'AT'),
        (r'^(?:Wiener Konzerthaus|Lorely-Saal|Rathausplatz Wien|Wiener Rathausplatz|'
         r'Stephansdom Wien|Wiener Stephansdom|Festsaal, Österreichische Akademie)',
         'Wien', 'AT'),
    )
    for pattern, city, country_code in rules:
        match = re.search(pattern, location, re.I)
        if match:
            venue = match.group(1).strip() if pattern.startswith('^Athen') else location
            return venue, city, country_code
    return None, None, None


def block_title(block, date_index):
    headings = [clean_text(item) for item in block[:date_index] if item.name in {'h3', 'h4'}]
    headings = [heading for heading in headings if heading.upper() not in {'ABGESAGT', 'VERSCHOBEN'}]
    return ' – '.join(headings)


def parse_events(html):
    soup = BeautifulSoup(html, 'html.parser')
    content = soup.select_one('.entry-content')
    if not content:
        raise ValueError('Concert page does not contain .entry-content')

    records = []
    for block in event_blocks(content):
        texts = [clean_text(item) for item in block]
        if any(text.upper() in {'ABGESAGT', 'VERSCHOBEN'} for text in texts):
            continue
        date_index = next((i for i, text in enumerate(texts) if PERFORMANCE_RE.search(text)), None)
        if date_index is None:
            continue
        title = block_title(block, date_index)
        if not title:
            continue
        description = '\n'.join(texts) or None
        link = next(
            (urljoin(EVENTS_URL, anchor.get('href')) for item in block
             for anchor in item.select('a[href]') if anchor.get('href')),
            EVENTS_URL,
        )

        for match in PERFORMANCE_RE.finditer(texts[date_index]):
            month = MONTHS.get(match.group(2).lower())
            try:
                event_date = date(int(match.group(3)), month, int(match.group(1))).isoformat()
            except (TypeError, ValueError):
                continue
            venue, city, country_code = resolve_location(match.group(5))
            if not venue or not city or not country_code:
                log_message(
                    'Skipped concert with unknown location',
                    event='crawler_item_skipped',
                    level='warning',
                    url=EVENTS_URL,
                    error_type='UnknownLocation',
                    error_message=clean_text(match.group(5)),
                )
                continue
            for raw_time in re.findall(r'\d{1,2}[:.]\d{2}', match.group(4)):
                hour, minute = re.split(r'[:.]', raw_time)
                records.append({
                    'title': title,
                    'date': event_date,
                    'url': link,
                    'time_from': f'{int(hour):02d}:{minute}',
                    'venue': venue,
                    'city': city,
                    'country_code': country_code,
                    'description': description,
                    'source_url': SOURCE_URL,
                    'source': SOURCE,
                })
    return records


class WienersingakademieAtCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='wienersingakademie_at',
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
        response = requests.get(EVENTS_URL, headers=HEADERS, timeout=45)
        response.raise_for_status()
        records = parse_events(response.text)
        return sorted(
            records,
            key=lambda item: (item['date'], item['time_from'], item['title'], item['venue']),
        )


def main():
    WienersingakademieAtCrawler().run()


if __name__ == '__main__':
    main()
