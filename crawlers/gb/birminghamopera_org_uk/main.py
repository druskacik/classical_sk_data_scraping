import re
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.birminghamopera.org.uk/'
ARCHIVE_URL = urljoin(SOURCE_URL, 'Blogs/past-productions?Take=1000')
SOURCE = 'Birmingham Opera Company'
CITY = 'Birmingham'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December',
        ),
        1,
    )
}


def clean_text(value):
    if not value:
        return ''
    if hasattr(value, 'get_text'):
        value = value.get_text('\n', strip=True)
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_soup(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def archive_links(soup):
    links = []
    for anchor in soup.select('a[href]'):
        url = urljoin(ARCHIVE_URL, anchor.get('href', ''))
        if '/blog/' in urlparse(url).path.lower() and url not in links:
            links.append(url)
    return links


def article_content(soup):
    article = soup.select_one('article')
    if not article:
        return '', '', None
    heading = article.select_one('h1')
    title = clean_text(heading) if heading else ''
    if not title:
        title = clean_text(soup.title).split('|', 1)[0].strip()

    # Publication dates and the site's recommendation/sidebar content are not
    # part of the production description.
    text = clean_text(article)
    text = re.split(r'\nPublished:\s*', text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r'^(?:About us\s+)?Past Productions\s+', '', text, flags=re.I)
    if title and text.startswith(title):
        text = text[len(title):].strip()
    return title, text, article


def related_production_url(title, article):
    if not article:
        return None
    key = re.sub(r'[^a-z0-9]+', '', re.sub(r'\b20\d{2}\b', '', title.lower()))
    if len(key) < 4:
        return None
    for anchor in article.select('a[href]'):
        label = re.sub(r'[^a-z0-9]+', '', clean_text(anchor).lower())
        url = urljoin(SOURCE_URL, anchor.get('href', ''))
        path = urlparse(url).path.lower()
        if label == key and '/blog/' not in path and url.startswith(SOURCE_URL):
            return url
    return None


def extract_dates(text, title):
    matches = []
    pattern = re.compile(
        r'(?P<first>\d{1,2})(?:st|nd|rd|th)?'
        r'(?:(?:\s*[-–]\s*|\s*/\s*)(?P<last>\d{1,2})(?:st|nd|rd|th)?)?'
        r'\s+(?P<month>' + '|'.join(MONTHS) + r')'
        r'(?:\s+(?P<year>20\d{2}))?',
        re.I,
    )
    title_year = re.search(r'\b(20\d{2})\b', title)
    for match in pattern.finditer(text):
        year = match.group('year') or (title_year.group(1) if title_year else None)
        if not year:
            continue
        first = int(match.group('first'))
        last = int(match.group('last') or first)
        if last < first or last - first > 14:
            continue
        for day in range(first, last + 1):
            try:
                matches.append(date(int(year), MONTHS[match.group('month').lower()], day).isoformat())
            except ValueError:
                continue

    # Some production headers use a compact list such as "28 / 29 / 30 April".
    list_pattern = re.compile(
        r'(?P<days>\d{1,2}(?:\s*/\s*\d{1,2}){2,})\s+'
        r'(?P<month>' + '|'.join(MONTHS) + r')(?:\s+(?P<year>20\d{2}))?',
        re.I,
    )
    for match in list_pattern.finditer(text):
        year = match.group('year') or (title_year.group(1) if title_year else None)
        if not year:
            continue
        for value in re.findall(r'\d{1,2}', match.group('days')):
            try:
                matches.append(
                    date(int(year), MONTHS[match.group('month').lower()], int(value)).isoformat()
                )
            except ValueError:
                continue
    return sorted(set(matches))


def extract_time(text):
    match = re.search(r'(?:show time|performance(?:s)?(?: at)?|starts? at)\s*[:\-]?\s*(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)', text, re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == 'pm' else 0)
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def extract_location(text):
    # Named venues are presented before their address on current production
    # pages. Keep the patterns narrow so prose and tour destinations do not
    # become placeholder venues.
    patterns = (
        r'((?:Forum|Theatre|Theater|Church|Hall|Arena|Warehouse|Centre|Center)\s+[A-Z][A-Za-z&\'’.-]*(?:\s+[A-Z][A-Za-z&\'’.-]*){0,3}),\s*\d',
        r'(St\.?\s+[A-Z][A-Za-z\'’.-]+(?:\s+Church)?),\s*(?:The\s+)?Bull Ring',
        r'(The Dream Tent)\s+at\s+Smithfield',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return clean_text(match.group(1)), CITY
    return None, None


def make_records(url, title, description, supplemental_text=''):
    combined = clean_text(f'{supplemental_text}\n{description}')
    venue, city = extract_location(combined)
    dates = extract_dates(combined, title)
    if not title or not venue or not city or not dates:
        return []
    time_from = extract_time(combined)
    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'GB',
            'description': description or None,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date in dates
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    archive = fetch_soup(session, ARCHIVE_URL)
    records = []
    for url in archive_links(archive):
        try:
            soup = fetch_soup(session, url)
            title, description, article = article_content(soup)
            supplemental = ''
            related_url = related_production_url(title, article)
            if related_url:
                try:
                    related = fetch_soup(session, related_url)
                    _, supplemental, _ = article_content(related)
                except requests.RequestException as error:
                    log_message(
                        'Failed to scrape related production page',
                        event='crawler_item_failed',
                        level='warning',
                        url=related_url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
            records.extend(make_records(url, title, description, supplemental))
        except requests.RequestException as error:
            log_message(
                'Failed to scrape production detail',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
    return sorted(records, key=lambda item: (item['date'], item['time_from'] or '', item['title']))


class BirminghamOperaOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='birminghamopera_org_uk',
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
    BirminghamOperaOrgUkCrawler().run()


if __name__ == '__main__':
    main()
