import re
import time
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.bamptonopera.org/'
ARCHIVE_URL = urljoin(SOURCE_URL, 'productions-past.htm')
SOURCE = 'Bampton Classical Opera'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

MONTHS = (
    'January|February|March|April|May|June|July|August|September|'
    'October|November|December'
)

# The archive normally prints only the venue, not its town. These mappings are
# limited to recurring, unambiguous Bampton Opera venues.
VENUE_CITIES = {
    'the deanery garden': 'Bampton',
    'the deanery gardens': 'Bampton',
    'deanery garden': 'Bampton',
    'cokethorpe school': 'Witney',
    'westonbirt school': 'Tetbury',
    'westonbirt orangery': 'Tetbury',
    'smith square hall': 'London',
    'smith square': 'London',
    "st john's smith square": 'London',
    'st john’s smith square': 'London',
    'the barn at old walland': 'Wadhurst',
}

EXPLICIT_CITIES = ('London', 'Oxford', 'Bampton', 'Witney', 'Cheltenham', 'Bath')


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    # The small site rate-limits bursts quite aggressively.
    time.sleep(0.35)
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def valid_date(day, month, year):
    try:
        return datetime.strptime(f'{day} {month} {year}', '%d %B %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(text):
    match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', text, re.I)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not 1 <= hour <= 12 or minute >= 60:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    if match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def city_for_venue(venue_text):
    folded = venue_text.casefold().strip(' .')
    for key, city in VENUE_CITIES.items():
        if key in folded:
            return city
    for city in EXPLICIT_CITIES:
        if re.search(rf'\b{re.escape(city)}\b', venue_text, re.I):
            return city
    return None


def clean_venue(value):
    venue = clean_text(value)
    venue = re.sub(r'\s+[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b.*$', '', venue, flags=re.I)
    venue = re.sub(r',?\s+(?:Oxfordshire|Gloucestershire)\b.*$', '', venue, flags=re.I)
    # Remove an explicit town only when the remaining text is a named venue.
    cities = '|'.join(EXPLICIT_CITIES)
    venue = re.sub(rf',\s*(?:{cities})\b.*$', '', venue, flags=re.I)
    return venue.strip(' ,-:')


def performances_from_line(line, year):
    """Parse both venue-first live listings and date-first archive listings."""
    text = clean_text(line)
    results = []
    venue_first = re.match(r'(.+?):\s*(.+)$', text)
    if venue_first and re.search(rf'\b(?:{MONTHS})\b', venue_first.group(2), re.I):
        location, date_text = venue_first.groups()
        venue = clean_venue(location)
        city = city_for_venue(location)
        event_time = parse_time(date_text)
        month_match = re.search(rf'\b({MONTHS})\b', date_text, re.I)
        if venue and city and month_match:
            month = month_match.group(1).title()
            for day in re.findall(r'\b(\d{1,2})(?:st|nd|rd|th)?\b', date_text[:month_match.start()]):
                event_date = valid_date(day, month, year)
                if event_date:
                    results.append((event_date, event_time, venue, city))
        return results

    date_first = re.match(rf'(.+?\b(?:{MONTHS})\b)\s*[-–—:]\s*(.+)$', text, re.I)
    if not date_first:
        return results
    date_text, location = date_first.groups()
    month_match = re.search(rf'\b({MONTHS})\b', date_text, re.I)
    venue = clean_venue(location)
    city = city_for_venue(location)
    event_time = parse_time(text)
    if not month_match or not venue or not city:
        return results
    month = month_match.group(1).title()
    for day in re.findall(r'\b(\d{1,2})(?:st|nd|rd|th)?\b', date_text[:month_match.start()]):
        event_date = valid_date(day, month, year)
        if event_date:
            results.append((event_date, event_time, venue, city))
    return results


def detail_record_data(soup):
    title = clean_text(soup.select_one('#content h1'))
    composer = clean_text(soup.select_one('#content h2'))
    if composer and composer.casefold() not in title.casefold():
        title = f'{composer}: {title}'
    sections = [clean_text(soup.select_one(selector)) for selector in ('#tab1', '#tab2', '#tab3')]
    description = '\n\n'.join(section for section in sections if section) or None
    info = soup.select_one('#tab1')
    lines = clean_text(info).splitlines() if info else []
    return title, description, lines


def archive_items(session):
    soup = get_soup(session, ARCHIVE_URL)
    items = []
    for card in soup.select('.featdiv2.box2'):
        link = card.select_one('a[href*="operadetail.htm?event="]')
        year_text = clean_text(card.select_one('.date'))
        year_match = re.search(r'\b(19\d{2}|20\d{2})\b', year_text)
        if link and year_match:
            items.append((urljoin(SOURCE_URL, link.get('href')), int(year_match.group(1))))
    return list(dict.fromkeys(items))


def live_items(session):
    soup = get_soup(session, SOURCE_URL)
    urls = {
        urljoin(SOURCE_URL, link.get('href'))
        for link in soup.select('a[href*="eventfuturedetail.htm?event="]')
    }
    return [(url, date.today().year) for url in sorted(urls)]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount(
        'https://',
        HTTPAdapter(max_retries=Retry(
            total=4,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=('GET',),
            respect_retry_after_header=True,
        )),
    )
    try:
        items = live_items(session) + archive_items(session)
    except requests.RequestException:
        raise

    records = []
    for url, year in items:
        try:
            soup = get_soup(session, url)
            title, description, lines = detail_record_data(soup)
        except requests.RequestException as error:
            log_message(
                'Failed to scrape production detail',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        if not title:
            continue
        for line in lines:
            for event_date, event_time, venue, city in performances_from_line(line, year):
                records.append({
                    'title': title,
                    'date': event_date,
                    'url': url,
                    'time_from': event_time,
                    'venue': venue,
                    'city': city,
                    'country_code': 'GB',
                    'description': description,
                    'source_url': SOURCE_URL,
                    'source': SOURCE,
                })
    return sorted(
        records,
        key=lambda row: (row['date'], row['time_from'] or '', row['title'], row['venue']),
    )


class BamptonOperaOrgCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bamptonopera_org',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
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
    BamptonOperaOrgCrawler().run()


if __name__ == '__main__':
    main()
