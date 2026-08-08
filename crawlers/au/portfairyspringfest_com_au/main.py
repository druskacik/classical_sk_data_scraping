import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://portfairyspringfest.com.au/'
SOURCE = 'Port Fairy Spring Music Festival'
PROGRAM_URL = urljoin(SOURCE_URL, 'program/')
CITY = 'Port Fairy'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}


def clean_text(element):
    if element is None:
        return ''
    text = element.get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_date_time(value, year):
    match = re.search(
        r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+'
        r'(\d{1,2}\s+[A-Za-z]+)(?:\s+20\d{2})?\s*,\s*'
        r'(\d{1,2}:\d{2})\s*(am|pm)\b',
        value,
        re.IGNORECASE,
    )
    if not match:
        return None, None

    try:
        event_date = datetime.strptime(
            f'{match.group(1)} {year}', '%d %B %Y'
        ).date().isoformat()
        time_from = datetime.strptime(
            f'{match.group(2)}{match.group(3)}', '%I:%M%p'
        ).strftime('%H:%M')
    except ValueError:
        return None, None
    return event_date, time_from


def metadata(event_soup):
    values = {}
    for item in event_soup.select('.event-meta__item'):
        label = clean_text(item.select_one('dt')).lower()
        value = clean_text(item.select_one('dd'))
        if label and value:
            values[label] = value
    return values


def parse_event(html, url, year):
    soup = BeautifulSoup(html, 'html.parser')
    title = clean_text(soup.select_one('h1.event-hero__title'))
    meta = metadata(soup)
    event_date, time_from = parse_date_time(meta.get('date', ''), year)
    venue = meta.get('venue', '').strip()
    if not title or not event_date or not venue:
        return None

    description_parts = []
    body = clean_text(soup.select_one('.event-body__main'))
    if body:
        description_parts.append(body)
    programme = clean_text(soup.select_one('.event-program'))
    if programme:
        description_parts.append(f'Programme\n{programme}')

    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': CITY,
        'country_code': 'AU',
        'description': '\n\n'.join(description_parts) or None,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


class PortFairySpringFestComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='portfairyspringfest_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='potential',
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
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            response = session.get(PROGRAM_URL, timeout=45)
            response.raise_for_status()
        except requests.RequestException as error:
            log_message(
                'Failed to fetch Port Fairy festival program',
                event='crawler_fetch_failed',
                level='error',
                url=PROGRAM_URL,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

        soup = BeautifulSoup(response.text, 'html.parser')
        heading = clean_text(soup.select_one('main'))
        years = [int(value) for value in re.findall(r'\b20\d{2}\b', heading)]
        year = max(years) if years else date.today().year
        event_urls = sorted({
            urljoin(PROGRAM_URL, link['href'])
            for link in soup.select('a[href*="/events/"]')
        })

        records = []
        for url in event_urls:
            try:
                event_response = session.get(url, timeout=45)
                event_response.raise_for_status()
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Port Fairy festival event',
                    event='crawler_fetch_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                continue
            record = parse_event(event_response.text, url, year)
            if record:
                records.append(record)

        return sorted(
            records,
            key=lambda record: (
                record['date'], record['time_from'] or '', record['title'], record['url']
            ),
        )


def main():
    PortFairySpringFestComAuCrawler().run()


if __name__ == '__main__':
    main()
