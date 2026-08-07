import re
import unicodedata
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://yelenagrinberg.com/'
CONCERTS_URL = urljoin(SOURCE_URL, 'concerts/')
SOURCE = 'Yelena Grinberg'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

DATE_RE = re.compile(
    r'(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?'
    r'(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+'
    r'(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>20\d{2})'
    r'(?:,?\s*(?:at\s*)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*'
    r'(?P<period>[ap])\.?m\.?)?',
    re.IGNORECASE,
)
CITY_RE = re.compile(
    r'(?P<city>New York|Brooklyn|Tarrytown|Lewiston|Livingston Manor|Princeton|'
    r'Bronxville|Eastchester|Nyack|Blue Hill|Montauk),\s*'
    r'(?P<state>AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)'
    r'(?:\s+\d{5}(?:-\d{4})?)?',
    re.IGNORECASE,
)
VENUE_WORD_RE = re.compile(
    r'\b(?:hall|church|club|university|college|museum|library|center|centre|'
    r'festival|pavilion|theatre|theater|gallery|auditorium|conservatory|school|'
    r'temple|synagogue|cathedral|consulate|association|residence|bar)\b',
    re.IGNORECASE,
)


def clean_text(value, separator=' '):
    text = value.get_text(separator, strip=True) if isinstance(value, Tag) else str(value or '')
    text = unicodedata.normalize('NFKC', text).replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def get_page(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def archive_urls(session):
    soup = get_page(session, CONCERTS_URL)
    urls = [CONCERTS_URL]
    for link in soup.select('.entry a[href]'):
        url = urljoin(CONCERTS_URL, link.get('href', '')).split('#', 1)[0]
        if re.match(r'^https://yelenagrinberg\.com/concerts(?:[/-]|$)', url) and url not in urls:
            urls.append(url)
    return urls


def event_sections(soup):
    entry = soup.select_one('.entry')
    if not entry:
        return
    headings = entry.find_all('h2')
    for heading in headings:
        title = clean_text(heading)
        if (
            not title
            or title.lower().startswith('concerts')
            or re.fullmatch(r'\d{4}\s*[-–]\s*\d{4}\s+Season', title, re.IGNORECASE)
        ):
            continue
        elements = []
        for sibling in heading.find_next_siblings():
            if sibling.name == 'h2':
                break
            if isinstance(sibling, Tag):
                elements.append(sibling)
        text = '\n'.join(clean_text(element) for element in elements if clean_text(element))
        if DATE_RE.search(text):
            yield title, elements, text


def resolve_location(elements):
    for element in elements:
        text = clean_text(element)
        city_match = CITY_RE.search(text)
        if not city_match:
            continue
        prefix = text[:city_match.start()].strip(' ,;-')
        if not VENUE_WORD_RE.search(prefix) or DATE_RE.search(prefix):
            continue
        # Addresses follow the venue name on these archive pages. Remove the
        # trailing street address while retaining numbered venue names.
        address = re.search(
            r'\s+\d+\s+(?:East|West|North|South|E\.?|W\.?|N\.?|S\.?)?\s*'
            r'[A-Za-z0-9 .\'-]+(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?)\b',
            prefix,
            re.IGNORECASE,
        )
        venue = prefix[:address.start()].strip(' ,;-') if address else prefix
        street_number = re.search(r'\s+\d+\s+', venue)
        if street_number:
            venue = venue[:street_number.start()].strip(' ,;-')
        venue = re.sub(r'^Presented by\s+(?:the\s+)?', '', venue, flags=re.IGNORECASE)
        venue = re.sub(r'\s+in$', '', venue, flags=re.IGNORECASE).strip()
        if venue and len(venue) <= 140:
            return venue, city_match.group('city').strip(), 'US'
    return None, None, None


def parse_datetime(match):
    raw_date = f"{match.group('month')} {match.group('day')} {match.group('year')}"
    try:
        event_date = datetime.strptime(raw_date, '%B %d %Y').date().isoformat()
    except ValueError:
        return None, None
    if not match.group('hour'):
        return event_date, None
    hour = int(match.group('hour'))
    minute = int(match.group('minute') or 0)
    period = match.group('period').lower()
    if not 1 <= hour <= 12 or minute > 59:
        return None, None
    hour = hour % 12 + (12 if period == 'p' else 0)
    return event_date, f'{hour:02d}:{minute:02d}'


def fragment(title, event_date, time_from):
    value = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode().lower()
    value = re.sub(r'[^a-z0-9]+', '-', value).strip('-')[:70]
    return f'{value}-{event_date}-{(time_from or "time-tba").replace(":", "")}'


def parse_archive(soup, page_url):
    records = []
    for title, elements, description in event_sections(soup):
        venue, city, country_code = resolve_location(elements)
        if not venue or not city:
            continue
        for match in DATE_RE.finditer(description):
            event_date, time_from = parse_datetime(match)
            if not event_date:
                continue
            records.append({
                'title': title,
                'date': event_date,
                'url': f'{page_url}#{fragment(title, event_date, time_from)}',
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': country_code,
                'description': description or None,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


class YelenaGrinbergComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='yelenagrinberg_com',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='US',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        records = []
        for url in archive_urls(session):
            try:
                records.extend(parse_archive(get_page(session, url), url))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert archive',
                    event='crawler_page_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
        unique = {(r['title'], r['date'], r['time_from'], r['venue']): r for r in records}
        return sorted(
            unique.values(),
            key=lambda record: (record['date'], record['time_from'] or '', record['title']),
        )


def main():
    YelenaGrinbergComCrawler().run()


if __name__ == '__main__':
    main()
