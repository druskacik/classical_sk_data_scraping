import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://cultureedinburgh.com/'
SOURCE = 'Culture Edinburgh'
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

DATE_PATTERN = re.compile(
    r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*'
    r'(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}),\s*(\d{1,2}:\d{2})\b'
)


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def canonical_url(url):
    parts = urlsplit(urljoin(SOURCE_URL, url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def sitemap_event_urls(session):
    soup = BeautifulSoup(get_response(session, SITEMAP_URL).content, 'xml')
    return sorted({
        canonical_url(node.get_text(strip=True))
        for node in soup.select('url > loc')
        if '/events/' in urlsplit(node.get_text(strip=True)).path
    })


def event_heading_section(soup):
    heading = soup.find('h1')
    return heading.find_parent('section') if heading else None


def event_datetime(section):
    if not section:
        return None
    for paragraph in section.find_all('p'):
        match = DATE_PATTERN.search(clean_text(paragraph))
        if not match:
            continue
        try:
            return datetime.strptime(
                f'{match.group(1)} {match.group(2)}', '%d %b %Y %H:%M'
            )
        except ValueError:
            return None
    return None


def event_venue(section):
    if not section:
        return ''
    for paragraph in section.find_all('p'):
        if DATE_PATTERN.search(clean_text(paragraph)):
            continue
        venue_link = paragraph.select_one('a[href*="/our-venues/"]')
        if venue_link:
            venue = clean_text(venue_link)
            if venue:
                return venue
        # Venue paragraphs put the postal address in a nested span. Taking the
        # direct text preserves the venue name without contaminating it.
        direct_text = clean_text(' '.join(
            child for child in paragraph.find_all(string=True, recursive=False)
        ))
        full_text = clean_text(paragraph)
        if direct_text and ('Edinburgh' in full_text or re.search(r'\bEH\d', full_text)):
            return direct_text
    return ''


def event_description(soup, html=''):
    candidates = []
    for node in soup.select('.payload-richtext'):
        text = clean_text(node)
        if text and not re.fullmatch(r'(?:From\s+)?£[\d.,]+', text):
            candidates.append(text)
    if candidates:
        return max(candidates, key=len)

    # Next.js keeps the Lexical long-description document in its streamed
    # server data even though the rendered tab is initially hidden.
    start = html.find('longDescription\\":')
    end = html.find('\\"image\\":', start)
    if start >= 0 and end > start:
        parts = []
        pattern = re.compile(r'\\"text\\":\\"((?:\\\\.|[^"\\])*)\\"')
        for match in pattern.finditer(html[start:end]):
            try:
                parts.append(json.loads(f'"{match.group(1)}"'))
            except json.JSONDecodeError:
                continue
        description = clean_text(' '.join(parts))
        if description:
            return description
    return None


def detail_record(session, url):
    response = get_response(session, url)
    soup = BeautifulSoup(response.content, 'html.parser')
    section = event_heading_section(soup)
    title = clean_text(section.find('h1')) if section else ''
    start = event_datetime(section)
    venue = event_venue(section)
    if not title or not start or not venue:
        return None

    return {
        'title': title,
        'date': start.date().isoformat(),
        'url': url,
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': 'Edinburgh',
        'country_code': 'GB',
        'description': event_description(soup, response.text),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = sitemap_event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(detail_record, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = future.result()
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Culture Edinburgh event detail',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
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


class CultureEdinburghComCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cultureedinburgh_com',
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
    CultureEdinburghComCrawler().run()


if __name__ == '__main__':
    main()
