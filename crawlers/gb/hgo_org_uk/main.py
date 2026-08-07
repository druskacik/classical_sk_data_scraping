import re
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://hgo.org.uk/'
PRODUCTIONS_API = urljoin(SOURCE_URL, 'wp-json/wp/v2/productions')
SOURCE = 'HGO'
CITY = 'London'

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
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(session, url, **kwargs):
    response = session.get(url, timeout=45, **kwargs)
    response.raise_for_status()
    return response


def production_urls(session):
    """Use the public WordPress API for the complete production archive."""
    response = get_response(
        session,
        PRODUCTIONS_API,
        params={'per_page': 100, '_fields': 'link'},
    )
    urls = {item.get('link') for item in response.json() if item.get('link')}

    # Current concerts are ordinary WordPress pages rather than archive items.
    soup = BeautifulSoup(get_response(session, SOURCE_URL).text, 'html.parser')
    ignored = {
        '', 'about', 'support-hgo', 'archive', 'productions', 'auditions',
        'press', 'advertising', 'success-stories', 'news', 'contact',
        'newsletter', 'hgo-newsletter-2', 'privacy-policy', 'hgoantiqua',
    }
    for anchor in soup.select('a[href]'):
        url = urljoin(SOURCE_URL, anchor.get('href'))
        parsed = urlparse(url)
        if parsed.netloc not in {'hgo.org.uk', 'www.hgo.org.uk'}:
            continue
        path = parsed.path.strip('/')
        if path and '/' not in path and path not in ignored:
            urls.add(url)
    return sorted(urls)


def labelled_value(lines, label):
    for index, line in enumerate(lines[:-1]):
        if line.upper().strip(':') == label:
            return lines[index + 1]
    return ''


def parse_date(value):
    value = re.sub(r'(?i)(\d)(st|nd|rd|th)\b', r'\1', value)
    # Month-only archive entries are not precise enough to be concert dates.
    if not re.search(
        r'(?i)(?:\b\d{1,2}\s*(?:[-–—]\s*\d{1,2}\s*)?[A-Z]{3,9}\b|'
        r'\b[A-Z]{3,9}\s+\d{1,2}\b)',
        value,
    ):
        return None
    # A range describes a production run. Store its explicitly printed first
    # performance rather than inventing performances on intervening days.
    year_match = re.search(r'\b(19|20)\d{2}\b', value)
    month_match = re.search(
        r'(?i)\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b',
        value,
    )
    day_matches = re.findall(r'(?<!\d)(\d{1,2})(?!\d)', value)
    if not year_match or not month_match or not day_matches:
        return None
    value = f'{day_matches[0]} {month_match.group(1)} {year_match.group(0)}'
    try:
        parsed = date_parser.parse(value, dayfirst=True, fuzzy=True)
        return date(parsed.year, parsed.month, parsed.day).isoformat()
    except (ValueError, OverflowError):
        return None


def extract_unlabelled_date(text):
    patterns = (
        r'(?i)\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+'
        r'(?:\d{1,2}\s+[A-Z][a-z]+|[A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?)\s+\d{4}\b',
        r'(?i)\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+\s+\d{4}\b',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ''


def make_record(url, html):
    soup = BeautifulSoup(html, 'html.parser')
    content = soup.select_one('.elementor-location-single') or soup.select_one('main')
    if not content:
        return None
    lines = [clean_text(line) for line in content.get_text('\n').splitlines()]
    lines = [line for line in lines if line]
    title_node = content.select_one('h1') or soup.select_one('h1')
    social_title = soup.select_one('meta[property="og:title"]')
    title = clean_text(
        social_title.get('content') if social_title else
        title_node.get_text(' ', strip=True) if title_node else ''
    )
    title = re.sub(r'\s+-\s+HGO$', '', title, flags=re.IGNORECASE)

    date_text = labelled_value(lines, 'DATES') or extract_unlabelled_date('\n'.join(lines[:30]))
    event_date = parse_date(date_text) if date_text else None
    venue_text = labelled_value(lines, 'VENUE')
    if not venue_text:
        # Current pages normally repeat a London venue immediately around the date.
        for line in lines[:30]:
            if re.search(r'(?i)\b(church|theatre|hall|arts centre|opera house)\b', line):
                venue_text = line
                break
    venue = clean_text(venue_text.split(',')[0])
    venue = re.split(r'\s+\d{1,2}[.:]\d{2}\b', venue, maxsplit=1)[0].strip()
    if re.search(r'(?i)\b(click|download|programme|donate|image)\b', venue):
        venue = ''
    if not title or not event_date or not venue:
        return None

    time_match = re.search(r'\b([01]?\d|2[0-3])[.:]([0-5]\d)\b', '\n'.join(lines[:30]))
    description = clean_text(content.get_text('\n', strip=True)) or None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': f'{int(time_match.group(1)):02d}:{time_match.group(2)}' if time_match else None,
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
    records = []
    for url in production_urls(session):
        try:
            record = make_record(url, get_response(session, url).text)
        except (requests.RequestException, ValueError) as error:
            log_message(
                'Failed to scrape concert detail',
                event='crawler_item_failed',
                level='warning',
                url=url,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            continue
        if record:
            records.append(record)
    return sorted(records, key=lambda item: (item['date'], item['title'], item['url']))


class HgoOrgUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='hgo_org_uk',
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
    HgoOrgUkCrawler().run()


if __name__ == '__main__':
    main()
