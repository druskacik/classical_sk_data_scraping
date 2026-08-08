import re
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.afcm.com.au/'
SOURCE = 'Australian Festival of Chamber Music'
PROGRAM_URL = urljoin(SOURCE_URL, 'afcm-festival/2026-festival-program/')

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

TIME_RE = re.compile(r'^(\d{1,2}):(\d{2})\s*(am|pm)\b', re.IGNORECASE)


def clean_text(element):
    if element is None:
        return ''
    text = element.get_text('\n', strip=True) if hasattr(element, 'get_text') else str(element)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date_heading(value, year):
    match = re.search(r'\b([A-Za-z]+)\s+(\d{1,2})\b', value)
    if not match or match.group(1).lower() not in MONTHS:
        return None
    try:
        return date(year, MONTHS[match.group(1).lower()], int(match.group(2))).isoformat()
    except ValueError:
        return None


def parse_time_and_location(value):
    match = TIME_RE.match(value)
    if not match:
        return None, clean_text(value)
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'pm':
        hour += 12
    return f'{hour:02d}:{int(match.group(2)):02d}', value[match.end():].strip()


def geography(title, location):
    if location == 'Glasgow':
        return 'Denise Glasgow Performing Arts Centre', 'Townsville'
    if location == 'Ayr':
        return 'Burdekin Theatre', 'Ayr'
    if location == 'Proserpine':
        return 'Proserpine Entertainment Centre', 'Proserpine'
    if not location:
        return None, None
    return location, 'Cairns'


def event_description(item):
    synopsis = clean_text(item.select_one('.item-main sunviewer'))
    synopsis = re.split(
        r'\b(?:Tickets start|SUBSCRIPTION INFORMATION)\b', synopsis, maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    programme = item.select_one('.program-info')
    if programme:
        heading = programme.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
        if heading:
            heading.decompose()
    programme_text = clean_text(programme)
    parts = [part for part in (synopsis, programme_text) if part]
    return '\n\n'.join(parts) or None


def parse_programme(html, page_url=PROGRAM_URL):
    soup = BeautifulSoup(html, 'html.parser')
    page_title = clean_text(soup.select_one('.program .page-title'))
    year_match = re.search(r'\b(20\d{2})\b', page_title)
    if not year_match:
        return []
    year = int(year_match.group(1))
    records = []

    for date_group in soup.select('.program .item-date'):
        event_date = parse_date_heading(clean_text(date_group.find('h2', recursive=False)), year)
        if not event_date:
            continue
        for item in date_group.select(':scope > .item'):
            title = clean_text(item.select_one('.item-main-text h3'))
            detail_link = item.select_one('.item-main-text a[href*="pid="]')
            metadata = clean_text(item.select_one('.item-main-text h6'))
            time_from, location = parse_time_and_location(metadata)
            venue, city = geography(title, location)
            if not title or not detail_link or not venue or not city:
                continue
            records.append({
                'title': title,
                'date': event_date,
                'url': urljoin(page_url, detail_link.get('href', '')),
                'time_from': time_from,
                'venue': venue,
                'city': city,
                'country_code': 'AU',
                'description': event_description(item),
            })

    return sorted(
        records,
        key=lambda record: (record['date'], record['time_from'] or '', record['title']),
    )


def fetch_html(url):
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return response


def discover_program_url(homepage_html):
    soup = BeautifulSoup(homepage_html, 'html.parser')
    link = soup.select_one('a[href*="festival-program"]')
    return urljoin(SOURCE_URL, link.get('href')) if link and link.get('href') else PROGRAM_URL


class AfcmComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='afcm_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='potential',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city', 'country_code',
            'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        try:
            homepage = fetch_html(SOURCE_URL)
            program_url = discover_program_url(homepage.text)
            response = fetch_html(program_url)
        except requests.RequestException as error:
            log_message(
                'Failed to fetch AFCM festival programme',
                event='crawler_fetch_failed',
                level='error',
                url=locals().get('program_url', SOURCE_URL),
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise
        return parse_programme(response.text, response.url)


def main():
    AfcmComAuCrawler().run()


if __name__ == '__main__':
    main()
