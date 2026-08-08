import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://cso.org.au/'
SITEMAP_URL = f'{SOURCE_URL}sitemap.xml'
SOURCE = 'Canberra Symphony Orchestra'
CITY = 'Canberra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
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


def get_xml(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'xml')


def sitemap_urls(session):
    index = get_xml(session, SITEMAP_URL)
    concert_sitemaps = [
        loc.get_text(strip=True)
        for loc in index.find_all('loc')
        if 'section-concerts' in loc.get_text(strip=True)
    ]
    if not concert_sitemaps:
        raise ValueError('Concert sitemap was not present in the sitemap index')

    urls = []
    for sitemap_url in concert_sitemaps:
        sitemap = get_xml(session, sitemap_url)
        for loc in sitemap.find_all('loc'):
            url = loc.get_text(strip=True)
            path = urlparse(url).path.rstrip('/')
            if re.fullmatch(r'/concerts/[^/]+', path):
                urls.append(url)
    return sorted(set(urls))


def labelled_value(soup, label):
    heading = soup.find(
        lambda tag: tag.name in ('h2', 'h3', 'h4')
        and clean_text(tag).casefold() == label.casefold()
    )
    if not heading:
        return ''
    value = heading.find_next_sibling()
    return clean_text(value)


def description_text(soup):
    parts = []
    for prose in soup.select('section.block--wysiwyg .prose'):
        content = []
        for child in prose.children:
            classes = child.get('class', []) if getattr(child, 'name', None) else []
            if child.name == 'div' and any('grid-cols-2' in item for item in classes):
                break
            text = clean_text(child)
            if text:
                content.append(text)
        text = clean_text('\n\n'.join(content))
        if text and text not in parts:
            parts.append(text)
    return clean_text('\n\n'.join(parts)) or None


def parse_datetime(value):
    normalized = clean_text(value)
    match = re.match(
        r'(?P<time>\d{1,2}:\d{2}\s*[ap]m)\s+'
        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+'
        r'(?P<date>\d{1,2}\s+[A-Za-z]+\s+\d{4})$',
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return None
    try:
        event_date = datetime.strptime(match.group('date'), '%d %B %Y').date().isoformat()
        event_time = datetime.strptime(
            re.sub(r'\s+', ' ', match.group('time')).upper(), '%I:%M %p'
        ).strftime('%H:%M')
    except ValueError:
        return None
    return event_date, event_time


def parse_event(session, url):
    soup = get_soup(session, url)
    title_meta = soup.find('meta', attrs={'property': 'og:title'})
    title = clean_text(title_meta.get('content')) if title_meta else clean_text(soup.find('h1'))
    venue = labelled_value(soup, 'Where')
    description = description_text(soup)
    if not title or not venue:
        return []

    records = []
    for time_tag in soup.find_all('time'):
        parsed = parse_datetime(time_tag)
        if not parsed:
            continue
        event_date, time_from = parsed
        records.append({
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': CITY,
            'country_code': 'AU',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = sitemap_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(parse_event, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except (requests.RequestException, ValueError) as error:
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
            record['date'], record['time_from'] or '', record['title'], record['url']
        ),
    )


class CsoOrgAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cso_org_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='classical',
        columns=[
            'title',
            'date',
            'url',
            'time_from',
            'venue',
            'city',
            'country_code',
            'description',
            'source_url',
            'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue'],
    )

    def scrape(self):
        return get_concerts()


def main():
    CsoOrgAuCrawler().run()


if __name__ == '__main__':
    main()
