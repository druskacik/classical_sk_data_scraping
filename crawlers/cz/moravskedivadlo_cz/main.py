import re
from datetime import date
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://moravskedivadlo.cz'
SOURCE_URL = f'{BASE_URL}/cs'
SOURCE = 'Moravské divadlo Olomouc'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; classical-bot/1.0)'}


def clean_text(value):
    value = unescape(value or '').replace('\xa0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def get_soup(session, url):
    response = session.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def parse_date(text, today=None):
    match = re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.', text)
    if not match:
        return None
    today = today or date.today()
    year = today.year
    month = int(match.group(2))
    if month < today.month - 6:
        year += 1
    return f'{year:04d}-{month:02d}-{int(match.group(1)):02d}'


def parse_time(text):
    match = re.search(r'\b(\d{1,2}):(\d{2})\b', text)
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def listing_events(session):
    events = []
    seen_pages = set()
    # The site's “show more” control loads the second programme page at /cs/1;
    # subsequent numeric URLs are unrelated article archive pages.
    for page_number in range(2):
        listing_url = SOURCE_URL if page_number == 0 else f'{SOURCE_URL}/{page_number}'
        soup = get_soup(session, listing_url)
        articles = soup.select('article.show')
        if not articles or listing_url in seen_pages:
            break
        seen_pages.add(listing_url)
        for article in articles:
            link = article.select_one('h2.title a[href]')
            date_el = article.select_one('.date')
            if not link or not date_el:
                continue
            date_text = clean_text(date_el.get_text(' ', strip=True))
            events.append({
                'title': clean_text(link.get_text(' ', strip=True)),
                'url': urljoin(BASE_URL, link['href']),
                'date': parse_date(date_text),
                'time_from': parse_time(date_text),
                'venue': clean_text(
                    article.select_one('.auditorium').get_text(' ', strip=True)
                    if article.select_one('.auditorium') else ''
                ) or None,
            })
    return events


def detail_description(session, url):
    soup = get_soup(session, url)
    main = soup.select_one('main')
    if not main:
        return None
    section = main.select_one('#o-inscenaci')
    if section:
        for tag in section.select('script, style, button, .show-more'):
            tag.decompose()
        text = clean_text(section.get_text('\n', strip=True))
    else:
        text = clean_text(main.get_text('\n', strip=True))
    return text or None


class MoravskeDivadloCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='moravskedivadlo_cz',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CZ',
        columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue', 'city', 'description', 'type'],
        dedupe_subset=['title', 'date', 'url', 'time_from'],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
    )

    def scrape(self):
        session = requests.Session()
        events = listing_events(session)
        descriptions = {}
        records = []
        for event in events:
            if event['url'] not in descriptions:
                descriptions[event['url']] = detail_description(session, event['url'])
            records.append({**event, 'time_to': None, 'city': 'Olomouc',
                            'description': descriptions[event['url']], 'type': 'concert'})
        return records


def main():
    MoravskeDivadloCrawler().run()


if __name__ == '__main__':
    main()
