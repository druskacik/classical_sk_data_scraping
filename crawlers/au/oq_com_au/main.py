import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.oq.com.au/'
CALENDAR_URL = urljoin(SOURCE_URL, 'whats-on/')
ARCHIVE_URL = urljoin(SOURCE_URL, 'past-events/')
SOURCE = 'Opera Queensland'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

# The schedule identifies venues but does not expose structured addresses for
# every performance. These venue/locality names provide strong geographic
# evidence without incorrectly applying Opera Queensland's home city to tours.
PLACE_HINTS = {
    'cairns': ('Cairns', 'AU'),
    'toowoomba': ('Toowoomba', 'AU'),
    'townsville': ('Townsville', 'AU'),
    'longreach': ('Longreach', 'AU'),
    'winton': ('Winton', 'AU'),
    'rockhampton': ('Rockhampton', 'AU'),
    'mackay': ('Mackay', 'AU'),
    'bundaberg': ('Bundaberg', 'AU'),
    'gladstone': ('Gladstone', 'AU'),
    'ipswich': ('Ipswich', 'AU'),
    'logan': ('Logan', 'AU'),
    'redland': ('Cleveland', 'AU'),
    'noosa': ('Noosa', 'AU'),
    'caloundra': ('Caloundra', 'AU'),
    'gold coast': ('Gold Coast', 'AU'),
    'brisbane': ('Brisbane', 'AU'),
    'south bank': ('Brisbane', 'AU'),
    'southbank': ('Brisbane', 'AU'),
    'qpac': ('Brisbane', 'AU'),
    'queensland performing arts centre': ('Brisbane', 'AU'),
    'opera queensland studio': ('Brisbane', 'AU'),
    'queensland conservatorium': ('Brisbane', 'AU'),
    'powerhouse': ('Brisbane', 'AU'),
    'the tivoli': ('Brisbane', 'AU'),
    'old museum': ('Brisbane', 'AU'),
    'city hall': ('Brisbane', 'AU'),
    'jimbour': ('Jimbour', 'AU'),
    'edinburgh': ('Edinburgh', 'GB'),
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def clean_inline(value):
    return re.sub(r'\s+', ' ', clean_text(value)).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def event_links(session):
    links = set()
    for listing_url in (CALENDAR_URL, ARCHIVE_URL):
        soup = get_soup(session, listing_url)
        for container in soup.select('.event-box-expanded'):
            link = container.find('a', href=True)
            if link:
                url = urljoin(listing_url, link['href']).split('#', 1)[0]
                if '/whats-on/' in url and url.rstrip('/') != CALENDAR_URL.rstrip('/'):
                    links.add(url)
    return sorted(links)


def parse_date(value):
    value = clean_text(value)
    value = re.sub(r'(?<=\d)(st|nd|rd|th)\b', '', value, flags=re.I)
    value = re.sub(r'^[A-Za-z]+,?\s+', '', value)
    for date_format in ('%B %d %Y', '%d %B %Y'):
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            pass
    return None


def parse_time(value):
    value = clean_text(value).lower().replace('.', '')
    match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', value)
    if not match:
        return None
    hour = int(match.group(1)) % 12 + (12 if match.group(3) == 'pm' else 0)
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def resolve_place(venue):
    normalized = venue.casefold()
    for hint, place in PLACE_HINTS.items():
        if hint in normalized:
            return place
    return None


def page_description(soup):
    overview = soup.select_one('.event-description #overview')
    if not overview:
        overview = soup.select_one('.event-description .event-content-box')
    return clean_text(overview) or None


def parse_event(url, soup):
    heading = soup.select_one('.ticket-details h2, .ticket-details h3')
    # The heading's nested span is the event category, not part of its title.
    title = clean_inline(' '.join(heading.find_all(string=True, recursive=False))) if heading else ''
    description = page_description(soup)
    records = []

    for schedule in soup.select('.schedule-program-detail'):
        venue = clean_inline(schedule.select_one('.event-location'))
        event_date = parse_date(schedule.select_one('.program-date'))
        place = resolve_place(venue)
        if not title or not venue or not event_date or not place:
            continue
        city, country_code = place
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': parse_time(schedule.select_one('.program-time')),
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_links(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_soup, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(parse_event(url, future.result()))
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class OqComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='oq_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
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
    OqComAuCrawler().run()


if __name__ == '__main__':
    main()
