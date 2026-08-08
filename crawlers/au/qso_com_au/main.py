import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://qso.com.au/'
SITEMAP_URL = urljoin(SOURCE_URL, 'sitemap.xml')
SOURCE = 'Queensland Symphony Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

VENUE_CITIES = {
    'brisbane-convention-and-exhibition-centre': 'Brisbane',
    'cairns-performing-arts-centre-cairns': 'Cairns',
    'charleville-town-hall': 'Charleville',
    'chinchilla-cultural-centre': 'Chinchilla',
    'concert-hall-qpac': 'Brisbane',
    'empire-theatre-toowoomba': 'Toowoomba',
    'fortitude-music-hall': 'Brisbane',
    'glasshouse-theatre-qpac': 'Brisbane',
    'hota-home-of-the-arts': 'Gold Coast',
    'lyric-theatre-qpac': 'Brisbane',
    'munro-martin-parklands-cairns': 'Cairns',
    'queensland-conservatorium-theatre-griffith-university': 'Brisbane',
    'queensland-symphony-orchestra-studio-south-bank': 'Brisbane',
    'roma-bungil-cultural-centre': 'Roma',
    'the-gabba': 'Brisbane',
    'townsville-civic-theatre': 'Townsville',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_soup(session, url, parser='html.parser'):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return BeautifulSoup(response.text, parser)


def event_urls(session):
    sitemap = get_soup(session, SITEMAP_URL, 'xml')
    urls = []
    for location in sitemap.find_all('loc'):
        url = clean_text(location)
        path = urlparse(url).path.rstrip('/')
        # Detail pages have a year, series, and event slug. Series landing
        # pages stop one path component earlier.
        if re.fullmatch(r'/events/\d{4}/[^/]+/[^/]+', path):
            urls.append(url)
    if not urls:
        raise ValueError('No event detail URLs were present in the sitemap')
    return sorted(set(urls))


def event_description(main, hero):
    parts = []
    for section in main.find_all('section', recursive=False):
        if section is hero:
            continue
        text = clean_text(section)
        if not text:
            continue
        heading = section.find(['h2', 'h3'])
        label = clean_text(heading).lstrip('#').casefold() if heading else ''
        if label == 'performances':
            break
        if label in {'music', 'program', 'programme'} or not label:
            if text not in parts:
                parts.append(text)
    return '\n\n'.join(parts) or None


def parse_event(session, url):
    soup = get_soup(session, url)
    main = soup.find('main')
    title_tag = main.find('h1') if main else None
    hero = title_tag.find_parent('section') if title_tag else None
    if not main or not hero:
        return []

    title = clean_text(title_tag)
    venue_link = hero.select_one('a[href*="/about/venues/"]')
    venue = clean_text(venue_link)
    venue_slug = urlparse(venue_link.get('href', '')).path.rstrip('/').split('/')[-1] if venue_link else ''
    city = VENUE_CITIES.get(venue_slug)
    if not title or not venue or not city:
        return []

    description = event_description(main, hero)
    datetimes = []
    for time_tag in hero.select('time[datetime]'):
        value = time_tag.get('datetime', '')
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            continue
        key = (parsed.date().isoformat(), parsed.strftime('%H:%M'))
        if key not in datetimes:
            datetimes.append(key)

    return [
        {
            'title': title,
            'date': event_date,
            'url': url,
            'time_from': time_from,
            'venue': venue,
            'city': city,
            'country_code': 'AU',
            'description': description,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        }
        for event_date, time_from in datetimes
    ]


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
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
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class QsoComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='qso_com_au',
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
    QsoComAuCrawler().run()


if __name__ == '__main__':
    main()
