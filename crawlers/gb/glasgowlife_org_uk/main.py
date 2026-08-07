import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.glasgowlife.org.uk/'
SOURCE = 'Glasgow Life'
LISTING_URL = urljoin(SOURCE_URL, 'whats-on')
CITY = 'Glasgow'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        text = value.get_text('\n', strip=True)
    else:
        text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, params=None):
    response = session.get(url, params=params, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def listing_urls(session):
    """Return the complete server-rendered Arts, Music and Culture catalogue."""
    urls = set()
    page = 1
    while True:
        soup = get_soup(
            session,
            LISTING_URL,
            params={'curPage': page, 'channel': 'Arts, Music and Culture'},
        )
        page_urls = {
            urljoin(SOURCE_URL, link['href'])
            for link in soup.select('article.card a[href*="/event/"]')
            if link.get('href')
        }
        new_urls = page_urls - urls
        urls.update(page_urls)
        next_link = soup.select_one(
            f'a[href*="curPage={page + 1}"][href*="channel="]'
        )
        if not new_urls or not next_link:
            break
        page += 1
    return urls


def parse_date(value):
    if not value:
        return None
    # The first explicit date is the event's catalogue date. For exhibitions or
    # recurring events the site presents a range; retaining its start avoids
    # inventing daily performances that the source does not claim.
    match = re.search(
        r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*'
        r'(\d{1,2})(?:st|nd|rd|th)?\s+'
        r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|'
        r'Dec(?:ember)?)(?:\s+(\d{4}))?\b',
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    year = match.group(3)
    if not year:
        years = re.findall(r'\b(?:19|20)\d{2}\b', value)
        year = years[0] if years else None
    if not year:
        return None
    normalized = f'{match.group(1)} {match.group(2)[:3]} {year}'
    try:
        return datetime.strptime(normalized.title(), '%d %b %Y').date().isoformat()
    except ValueError:
        return None


def parse_time(value):
    if not value:
        return None
    match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', value, re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'pm':
        hour += 12
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def detail_value(soup, heading):
    for item in soup.select('.event-details__item'):
        title = item.select_one('.event-details__title')
        if clean_text(title).casefold() == heading.casefold():
            return clean_text(item.select_one('.event-details__bd'))
    return ''


def detail_record(session, url):
    soup = get_soup(session, url)
    title = clean_text(soup.select_one('h1.subsite-head__title, h1'))
    date_and_time = detail_value(soup, 'Dates and times')
    date = parse_date(date_and_time)
    venue = detail_value(soup, 'Venue')
    venue = re.sub(r'\n?View map.*$', '', venue, flags=re.I | re.S).strip()
    description = clean_text(soup.select_one('.editor--article')) or None

    if not title or not date or not venue:
        return None
    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': parse_time(date_and_time),
        'venue': venue,
        'city': CITY,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = listing_urls(session)
    records = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(detail_record, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = future.result()
                if record:
                    records.append(record)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Glasgow Life event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class GlasgowLifeOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='glasgowlife_org_uk',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='GB',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    GlasgowLifeOrgUkCrawler().run()


if __name__ == '__main__':
    main()
