import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.orchestravictoria.com.au/'
SOURCE = 'Orchestra Victoria'
SITEMAP_URL = f'{SOURCE_URL}sitemaps-1-section-productions-1-sitemap.xml'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            '', 'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December',
        )
    )
    if name
}
DATE_RE = re.compile(
    r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*'
    r'(\d{1,2})(?:st|nd|rd|th)?\s+'
    r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    r'(?:\s+(20\d{2}))?',
    re.IGNORECASE,
)
TIME_RE = re.compile(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', re.IGNORECASE)


def clean_text(value):
    if value is None:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch(session, url):
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response


def catalogue_urls(session):
    soup = BeautifulSoup(fetch(session, SITEMAP_URL).text, 'xml')
    prefix = f'{SOURCE_URL}performances/'
    return sorted({loc.get_text(strip=True) for loc in soup.find_all('loc') if loc.get_text(strip=True).startswith(prefix)})


def page_modified_date(soup):
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            payload = json.loads(script.string or '')
        except (TypeError, ValueError):
            continue
        nodes = payload.get('@graph', []) if isinstance(payload, dict) else []
        for node in nodes:
            value = node.get('dateModified') or node.get('datePublished')
            if value:
                try:
                    return datetime.fromisoformat(value).date()
                except ValueError:
                    pass
    return None


def parse_date(value, reference_date=None):
    match = DATE_RE.search(value)
    if not match:
        return None
    month = MONTHS[match.group(2).lower()]
    if match.group(3):
        year = int(match.group(3))
    elif reference_date:
        year = reference_date.year + (month < reference_date.month)
    else:
        return None
    try:
        return date(year, month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def parse_time(value):
    match = TIME_RE.search(value)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'pm':
        hour += 12
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def page_title(main):
    heading = main.find('h1')
    return clean_text(heading).replace('\n', ': ') if heading else ''


def event_description(main):
    # The full page body intentionally retains programme, composer and work text.
    text = clean_text(main)
    return text or None


def location_records(soup, url):
    main = soup.find('main')
    if not main:
        return []
    title = page_title(main)
    if not title:
        return []
    reference_date = page_modified_date(soup)
    description = event_description(main)
    records = []

    for item in main.find_all('li'):
        paragraphs = item.find_all('p', recursive=False)
        if len(paragraphs) < 2:
            continue
        date_text = clean_text(paragraphs[0])
        event_date = parse_date(date_text, reference_date)
        if not event_date:
            continue
        city = clean_text(item.find('strong', recursive=False))
        address_lines = [clean_text(part) for part in paragraphs[1].stripped_strings]
        address_lines = [part for part in address_lines if part]
        venue = address_lines[0] if address_lines else ''
        if not city or not venue:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': parse_time(date_text),
            'venue': venue,
            'city': city,
            'country_code': 'AU',
            'description': description,
        })
    return records


def parse_performance(html, url):
    return location_records(BeautifulSoup(html, 'html.parser'), url)


class OrchestraVictoriaComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='orchestravictoria_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        urls = catalogue_urls(session)
        records = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch, session, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    records.extend(parse_performance(future.result().text, url))
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch Orchestra Victoria performance',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(records, key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue'],
        ))


def main():
    OrchestraVictoriaComAuCrawler().run()


if __name__ == '__main__':
    main()
