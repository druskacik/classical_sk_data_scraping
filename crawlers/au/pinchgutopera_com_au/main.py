import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.pinchgutopera.com.au/'
ARCHIVE_URL = urljoin(SOURCE_URL, 'past-productions')
SOURCE = 'Pinchgut Opera'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        'Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

MONTHS = {
    month.lower(): number
    for number, month in enumerate(
        [
            'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December',
        ],
        1,
    )
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_location(value):
    """Return a venue/city only where the site's location is unambiguous."""
    location = clean_text(value)
    if (
        not location
        or ' and ' in location.lower()
        or ' & ' in location
        or ',' not in location
    ):
        return None
    venue, locality = [part.strip() for part in location.rsplit(',', 1)]
    if not venue or not locality:
        return None
    city = {
        'southbank': 'Melbourne',
        'walsh bay': 'Sydney',
    }.get(locality.lower(), locality)
    return venue, city


def parse_time(value):
    match = re.search(r'\b(\d{1,2}):(\d{2})\s*([AP]M)\b', value, re.I)
    if not match:
        return None
    parsed = datetime.strptime(''.join(match.groups()).upper(), '%I%M%p')
    return parsed.strftime('%H:%M')


def parse_performance_date(value, year):
    match = re.search(
        r'\b(January|February|March|April|May|June|July|August|September|'
        r'October|November|December)\s+(\d{1,2})\b',
        value,
        re.I,
    )
    if not match:
        return None
    try:
        return date(year, MONTHS[match.group(1).lower()], int(match.group(2))).isoformat()
    except ValueError:
        return None


def parse_leading_date(value):
    """Parse the first real date in a production's published date label."""
    text = value.replace('–', '-').replace('—', '-')
    year_match = re.search(r'\b(20\d{2})\b', text)
    if not year_match:
        return None
    year = int(year_match.group(1))
    month_match = re.search(
        r'\b(January|February|March|April|May|June|July|August|September|'
        r'October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|'
        r'Oct|Nov|Dec)\b',
        text,
        re.I,
    )
    if not month_match:
        return None
    month_name = month_match.group(1)
    month = datetime.strptime(month_name[:3], '%b').month
    before_month = text[:month_match.start()]
    day_matches = re.findall(r'\b(\d{1,2})\b', before_month)
    if day_matches:
        day = int(day_matches[-1])
    else:
        after_month = text[month_match.end():]
        day_match = re.search(r'\b(\d{1,2})\b', after_month)
        if not day_match:
            return None
        day = int(day_match.group(1))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def hero_date_locations(soup):
    container = soup.select_one('.title-container .portable-text')
    if not container:
        return []
    paragraphs = container.find_all('p', recursive=False)
    pairs = []
    for index, paragraph in enumerate(paragraphs):
        strong = paragraph.find('strong')
        if not strong:
            continue
        date_text = clean_text(strong)
        if not re.search(r'\b20\d{2}\b', date_text):
            continue
        # Newer production headers place the date and venue in one paragraph,
        # separated by a line break after the strong date text.
        paragraph_copy = BeautifulSoup(str(paragraph), 'html.parser')
        copied_strong = paragraph_copy.find('strong')
        if copied_strong:
            copied_strong.decompose()
        inline_location = clean_text(paragraph_copy)
        if inline_location:
            pairs.append((date_text, inline_location))
            continue
        for following in paragraphs[index + 1:]:
            location_text = clean_text(following)
            if location_text:
                pairs.append((date_text, location_text))
                break
    return pairs


def performance_items(soup):
    label = soup.find(
        lambda tag: tag.name == 'p' and clean_text(tag) == 'Performances'
    )
    if not label:
        return []
    section = label.parent
    return [clean_text(item) for item in section.select('ul > li > p')]


def production_records(session, url):
    soup = get_soup(session, url)
    main = soup.select_one('main')
    title_node = main.find('h1') if main else None
    title = clean_text(title_node)
    pairs = hero_date_locations(soup)
    if not title or not main or not pairs:
        return []

    description = clean_text(main)
    primary_location = parse_location(pairs[0][1])
    year_match = re.search(r'\b(20\d{2})\b', pairs[0][0])
    records = []
    exact_dates = set()

    if primary_location and year_match:
        venue, city = primary_location
        for item in performance_items(soup):
            event_date = parse_performance_date(item, int(year_match.group(1)))
            if not event_date:
                continue
            exact_dates.add(event_date)
            records.append(
                make_record(title, event_date, url, parse_time(item), venue, city, description)
            )

    # Archives generally retain a production date range but not the old list of
    # individual performances. Preserve its first explicitly published date.
    # This also captures separately listed tour dates absent from Performances.
    for date_text, location_text in pairs:
        event_date = parse_leading_date(date_text)
        location = parse_location(location_text)
        if not event_date or not location or event_date in exact_dates:
            continue
        venue, city = location
        records.append(make_record(title, event_date, url, None, venue, city, description))

    return records


def make_record(title, event_date, url, time_from, venue, city, description):
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'AU',
        'description': description or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = set()
    for listing_url in (SOURCE_URL, ARCHIVE_URL):
        soup = get_soup(session, listing_url)
        for link in soup.select('a[href*="/shows/"]'):
            urls.add(urljoin(SOURCE_URL, link.get('href')).split('#', 1)[0])

    records = []
    for url in sorted(urls):
        try:
            records.extend(production_records(session, url))
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
        key=lambda item: (
            item['date'], item['time_from'] or '', item['title'], item['venue']
        ),
    )


class PinchgutOperaComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='pinchgutopera_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
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
    PinchgutOperaComAuCrawler().run()


if __name__ == '__main__':
    main()
