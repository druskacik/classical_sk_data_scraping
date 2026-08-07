import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://bsolive.com/'
LISTING_URL = f'{SOURCE_URL}whats-on/'
SOURCE = 'Bournemouth Symphony Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def catalogue_items(session):
    first_page = get_soup(session, LISTING_URL)
    months = []
    for option in first_page.select('select[name="events-date"] option[value]'):
        try:
            months.append(date.fromisoformat(option['value']))
        except ValueError:
            continue

    pages = [(None, first_page)]
    for month in months:
        url = f'{LISTING_URL}?{urlencode({"events-date": month.isoformat()})}'
        pages.append((month, get_soup(session, url)))

    items = {}
    for selected_month, soup in pages:
        for card in soup.select('section.listing.overview .tease-events'):
            anchor = card.select_one('h3 a[href*="/events/"]')
            date_text = clean_text(card.select_one('.post-date'))
            if not anchor or not date_text:
                continue
            url = anchor.get('href', '').split('#', 1)[0]
            if not url:
                continue
            key = (url, selected_month.isoformat() if selected_month else date_text)
            items[key] = {
                'url': url,
                'title': clean_text(anchor),
                'date_text': date_text,
                'selected_month': selected_month,
            }
    return list(items.values())


def parse_time(text):
    match = re.search(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', text, re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour < 1 or hour > 12 or minute > 59:
        return None
    if match.group(3).lower() == 'pm' and hour != 12:
        hour += 12
    elif match.group(3).lower() == 'am' and hour == 12:
        hour = 0
    return f'{hour:02d}:{minute:02d}'


def parse_performance_date(text, year_hint, month_hint=None):
    matches = re.findall(
        r'\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\b',
        text,
        re.IGNORECASE,
    )
    if not matches:
        return None
    day_text, month_text = matches[-1]
    month = MONTHS[month_text.lower()]
    year = year_hint
    if month_hint is not None:
        if month < month_hint - 6:
            year += 1
        elif month > month_hint + 6:
            year -= 1
    try:
        return date(year, month, int(day_text)).isoformat()
    except ValueError:
        return None


def city_from_venue(venue_block, listing_venue=''):
    address_parts = [clean_text(node) for node in venue_block.select('.venue-details > span')]
    candidates = address_parts + [listing_venue]
    for value in candidates:
        parts = [part.strip() for part in value.split(',') if part.strip()]
        if len(parts) >= 2:
            city = re.sub(r'\s+[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$', '', parts[-1], flags=re.I)
            if city and not re.search(r'\d', city):
                return city
    return None


def description_from(soup):
    body = soup.select_one('.article-content .article-body')
    if not body:
        return None
    body = BeautifulSoup(str(body), 'html.parser')
    for element in body.select('script, style, .supporters'):
        element.decompose()
    return clean_text(body) or None


def parse_detail(session, item):
    soup = get_soup(session, item['url'])
    title = clean_text(soup.select_one('main h1')) or item['title']
    # The h1 also contains subtitle and venue nodes; retain only its first text node.
    heading = soup.select_one('main h1')
    if heading:
        direct_text = clean_text(next((x for x in heading.contents if isinstance(x, str)), ''))
        title = direct_text or item['title']
    description = description_from(soup)

    selected_month = item['selected_month']
    if selected_month:
        year_hint, month_hint = selected_month.year, selected_month.month
    else:
        current = date.today()
        year_hint, month_hint = current.year, current.month

    listing_venue = ''
    listing_date = item['date_text']
    records = []
    for event_info in soup.select('main .event-info:not(.event-info-online)'):
        venue_block = event_info.select_one('.event-venue')
        venue = clean_text(venue_block.select_one('.venue-details h3')) if venue_block else ''
        city = city_from_venue(venue_block, listing_venue) if venue_block else None
        if not venue or not city:
            continue
        for instance in event_info.select('.instances > .instance'):
            date_time_text = clean_text(instance.select_one('h3'))
            event_date = parse_performance_date(date_time_text, year_hint, month_hint)
            if not event_date:
                event_date = parse_performance_date(listing_date, year_hint, month_hint)
            if not event_date:
                continue
            records.append({
                'title': title,
                'date': event_date,
                'url': item['url'],
                'time_from': parse_time(date_time_text),
                'venue': venue,
                'city': city,
                'country_code': 'GB',
                'description': description,
                'source_url': SOURCE_URL,
                'source': SOURCE,
            })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    items = catalogue_items(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(parse_detail, session, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape concert detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=item['url'],
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    unique = {
        (row['title'], row['date'], row['time_from'], row['venue'], row['url']): row
        for row in records
    }
    return sorted(unique.values(), key=lambda row: (
        row['date'], row['time_from'] or '', row['title'], row['venue'],
    ))


class BsoliveComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='bsolive_com',
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
    BsoliveComCrawler().run()


if __name__ == '__main__':
    main()
