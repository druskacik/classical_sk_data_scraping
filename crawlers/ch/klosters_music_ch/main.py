import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://klosters-music.ch/'
SITEMAP_URL = f'{SOURCE_URL}wp-sitemap.xml'
SOURCE = 'Klosters Music'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-CH,de;q=0.9,en;q=0.7',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def event_urls(session):
    """Return every German event URL published in the WordPress sitemaps."""
    index = BeautifulSoup(get_response(session, SITEMAP_URL).text, 'xml')
    sitemap_urls = [
        node.get_text(strip=True)
        for node in index.select('loc')
        if 'wp-sitemap-posts-event-' in node.get_text()
        and '/en/' not in node.get_text()
    ]
    urls = []
    for sitemap_url in sitemap_urls:
        sitemap = BeautifulSoup(get_response(session, sitemap_url).text, 'xml')
        urls.extend(node.get_text(strip=True) for node in sitemap.select('loc'))
    return list(dict.fromkeys(urls))


def input_value(form, name):
    field = form.select_one(f'input[name="{name}"]') if form else None
    return clean_text(field.get('value')) if field else ''


def resolve_city(venue):
    value = venue.lower()
    if 'bad ragaz' in value:
        return 'Bad Ragaz'
    # Older Klosters Music pages use shortened names for the same local
    # venues used by recent pages. Bahnhofplatz and Alp Madrisa are also
    # unambiguously Klosters locations in this institution's catalogue.
    klosters_venues = (
        'klosters',
        'arena',
        'kirche st. jakob',
        'atelier bolt',
        'bahnhofplatz',
        'alp madrisa',
    )
    if any(name in value for name in klosters_venues):
        return 'Klosters'
    return None


def make_record(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    form = soup.select_one('form.ics')
    heading = soup.select_one('.detail__heading-1')

    title = clean_text(heading) if heading else input_value(form, 'summary')
    start = input_value(form, 'date_start')
    venue = input_value(form, 'location')
    if not title or not start or not venue:
        return None

    try:
        starts_at = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None

    city = resolve_city(venue)
    if not city:
        return None

    description_parts = []
    lead = soup.select_one('.detail__lead')
    if lead:
        description_parts.append(clean_text(lead))
    for body in soup.select('.content-text-area'):
        text = clean_text(body)
        if text and text not in description_parts:
            description_parts.append(text)

    return {
        'title': title,
        'date': starts_at.date().isoformat(),
        'url': url,
        'time_from': starts_at.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'CH',
        'description': '\n\n'.join(description_parts) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = event_urls(session)
    records = []

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(get_response, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                record = make_record(url, future.result().text)
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Klosters Music event',
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


class KlostersMusicChCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='klosters_music_ch',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='CH',
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
    KlostersMusicChCrawler().run()


if __name__ == '__main__':
    main()
