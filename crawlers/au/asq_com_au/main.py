import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import unescape

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://asq.com.au/'
SOURCE = 'Australian String Quartet'
API_URL = f'{SOURCE_URL}wp-json/wp/v2/liveconcerts'

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
    r'(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\s+)?'
    r'(\d{1,2})(?:st|nd|rd|th)?\s+'
    r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    r'(?:\s*,?\s*(20\d{2}))?',
    re.IGNORECASE,
)
TIME_RE = re.compile(r'\b(\d{1,2})(?:[.:](\d{2}))?\s*(am|pm)\b', re.IGNORECASE)
SHORT_RANGE_RE = re.compile(
    r'(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\s+)?(\d{1,2})(?:st|nd|rd|th)?\s*'
    r'[—–-]\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\s+)?\d{1,2}(?:st|nd|rd|th)?\s+'
    r'(January|February|March|April|May|June|July|August|September|October|November|December)',
    re.IGNORECASE,
)


def clean_text(element):
    if element is None:
        return ''
    text = element.get_text('\n', strip=True) if hasattr(element, 'get_text') else str(element)
    text = unescape(text).replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date(value, default_year=None):
    explicit_year = re.search(r'\b(20\d{2})\b', value)
    inferred_year = int(explicit_year.group(1)) if explicit_year else default_year
    short_range = SHORT_RANGE_RE.search(value)
    if short_range and inferred_year:
        try:
            return date(
                int(inferred_year),
                MONTHS[short_range.group(2).lower()],
                int(short_range.group(1)),
            ).isoformat()
        except ValueError:
            return None

    match = DATE_RE.search(value)
    if not match:
        return None
    year = int(match.group(3) or inferred_year or 0)
    if not year:
        return None
    try:
        return date(year, MONTHS[match.group(2).lower()], int(match.group(1))).isoformat()
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


def description_from_api(item):
    soup = BeautifulSoup(item.get('content', {}).get('rendered', ''), 'html.parser')
    for unwanted in soup.select(
        'script, style, .elementor-widget-button, .elementor-widget-video, '
        '.elementor-widget-image, .elementor-widget-divider, .elementor-widget-spacer'
    ):
        unwanted.decompose()
    text = clean_text(soup)
    return text or clean_text(BeautifulSoup(
        item.get('excerpt', {}).get('rendered', ''), 'html.parser'
    )) or None


def metadata_wrappers(soup):
    wrappers = soup.select('.dce-meta-wrapper')
    title_wrapper = next((wrapper for wrapper in wrappers if wrapper.select_one('h1')), None)
    detail_wrapper = next(
        (wrapper for wrapper in wrappers if wrapper is not title_wrapper and wrapper.select_one('h2')),
        None,
    )
    return title_wrapper, detail_wrapper


def parse_single_event(title, url, description, detail_wrapper):
    heading = detail_wrapper.select_one('h2')
    if heading is None:
        return []

    city = next((text.strip() for text in heading.find_all(string=True, recursive=False) if text.strip()), '')
    venue_link = heading.select_one('.ipn a')
    venue = clean_text(venue_link)
    detail_text = clean_text(detail_wrapper)
    event_date = parse_date(detail_text)
    if not title or not city or not venue or not event_date:
        return []

    return [{
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': parse_time(detail_text),
        'venue': venue,
        'city': city,
        'country_code': 'AU',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }]


def schedule_lines(soup):
    heading = next(
        (
            element for element in soup.find_all(['h2', 'h3'])
            if 'dates and venues' in clean_text(element).lower()
        ),
        None,
    )
    section = heading.find_parent('section') if heading else None
    if not heading or not section:
        return []
    lines = [line.strip() for line in clean_text(section).splitlines() if line.strip()]
    start = next((index + 1 for index, line in enumerate(lines) if 'dates and venues' in line.lower()), 0)
    end = next(
        (index for index in range(start, len(lines)) if lines[index].lower() in {'program', 'programme'}),
        len(lines),
    )
    return lines[start:end]


def parse_tour(title, url, description, soup, detail_text):
    lines = schedule_lines(soup)
    year_match = re.search(r'\b(20\d{2})\b', detail_text)
    default_year = int(year_match.group(1)) if year_match else None
    records = []

    date_indexes = [index for index, line in enumerate(lines) if DATE_RE.search(line)]
    for position, index in enumerate(date_indexes):
        if index == 0:
            continue
        city = lines[index - 1]
        next_index = date_indexes[position + 1] - 1 if position + 1 < len(date_indexes) else len(lines)
        venue_parts = lines[index + 1:next_index]
        event_date = parse_date(lines[index], default_year)
        venue = ', '.join(venue_parts)
        city = re.sub(r'\s*\([A-Z]{2,3}\)\s*$', '', city).strip()
        if not title or not city or city.lower() == 'multiple locations' or not venue or not event_date:
            continue
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': parse_time(lines[index]),
            'venue': venue,
            'city': city,
            'country_code': 'AU',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def parse_event_page(item, html):
    soup = BeautifulSoup(html, 'html.parser')
    title_wrapper, detail_wrapper = metadata_wrappers(soup)
    title = clean_text(title_wrapper.select_one('h1')) if title_wrapper else ''
    title = title or clean_text(BeautifulSoup(item.get('title', {}).get('rendered', ''), 'html.parser'))
    if detail_wrapper is None:
        return []

    url = item.get('link', '')
    description = description_from_api(item)
    detail_text = clean_text(detail_wrapper)
    if 'multiple locations' in detail_text.lower():
        return parse_tour(title, url, description, soup, detail_text)
    return parse_single_event(title, url, description, detail_wrapper)


class AsqComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='asq_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city', 'country_code',
            'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def fetch_catalogue(self, session):
        items = []
        page = 1
        while True:
            response = session.get(
                API_URL,
                params={
                    'per_page': 100,
                    'page': page,
                    '_fields': 'link,title,content,excerpt',
                },
                timeout=60,
            )
            if response.status_code == 400 and page > 1:
                break
            response.raise_for_status()
            batch = response.json()
            items.extend(batch)
            total_pages = int(response.headers.get('X-WP-TotalPages', page))
            if page >= total_pages:
                break
            page += 1
        return items

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            items = self.fetch_catalogue(session)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to fetch Australian String Quartet event catalogue',
                event='crawler_fetch_failed',
                level='error',
                url=API_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        def fetch(item):
            response = requests.get(item['link'], headers=HEADERS, timeout=60)
            response.raise_for_status()
            return parse_event_page(item, response.text)

        records = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch, item): item for item in items if item.get('link')}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    records.extend(future.result())
                except (requests.RequestException, ValueError) as error:
                    log_message(
                        'Failed to fetch Australian String Quartet event',
                        event='crawler_event_fetch_failed',
                        level='warning',
                        url=item.get('link'),
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['venue']
            ),
        )


def main():
    AsqComAuCrawler().run()


if __name__ == '__main__':
    main()
