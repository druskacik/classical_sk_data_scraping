import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.operanorth.co.uk/'
SOURCE = 'Opera North'
SITEMAP_URL = f'{SOURCE_URL}sitemap-posttype-event.xml'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-GB,en;q=0.9',
}

# Opera North tours, so the Leeds default must not be applied to performances.
# These are venues occurring in its published calendar and archive.
VENUE_CITIES = {
    'howard assembly room': 'Leeds',
    'howard opera centre': 'Leeds',
    'leeds grand theatre': 'Leeds',
    'leeds town hall': 'Leeds',
    'clothworkers centenary concert hall': 'Leeds',
    'the venue, leeds conservatoire': 'Leeds',
    'leeds cathedral': 'Leeds',
    'kirkgate market': 'Leeds',
    'millennium square': 'Leeds',
    'the wardrobe': 'Leeds',
    'dewsbury town hall': 'Dewsbury',
    'huddersfield town hall': 'Huddersfield',
    'huddersfield parish church': 'Huddersfield',
    'lawrence batley theatre': 'Huddersfield',
    'ripon cathedral': 'Ripon',
    'theatre royal, newcastle': 'Newcastle upon Tyne',
    'newcastle theatre royal': 'Newcastle upon Tyne',
    'theatre royal newcastle': 'Newcastle upon Tyne',
    'the lowry': 'Salford',
    'lowry, salford quays': 'Salford',
    'nottingham theatre royal': 'Nottingham',
    'theatre royal, nottingham': 'Nottingham',
    'theatre royal nottingham': 'Nottingham',
    'hull new theatre': 'Hull',
    'hull city hall': 'Hull',
    'city hall, hull': 'Hull',
    'sheffield city hall': 'Sheffield',
    'buxton opera house': 'Buxton',
    'the sage gateshead': 'Gateshead',
    'sage gateshead': 'Gateshead',
    'the glasshouse international centre for music': 'Gateshead',
    'royal concert hall, nottingham': 'Nottingham',
    'royal hall, harrogate': 'Harrogate',
    'harrogate theatre': 'Harrogate',
    'st george’s hall, bradford': 'Bradford',
    "st george's hall, bradford": 'Bradford',
    'bradford cathedral': 'Bradford',
    'york theatre royal': 'York',
    'york minster': 'York',
    'grand opera house, york': 'York',
    'venue cymru': 'Llandudno',
    'the bridgewater hall': 'Manchester',
    'royal festival hall': 'London',
    'barbican centre': 'London',
}


def clean_text(value):
    if not value:
        return ''
    text = BeautifulSoup(str(value), 'html.parser').get_text('\n', strip=True)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def get_response(url):
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    return response


def event_urls():
    soup = BeautifulSoup(get_response(SITEMAP_URL).content, 'xml')
    urls = []
    for node in soup.select('url > loc'):
        url = clean_text(node)
        parsed = urlparse(url)
        if parsed.netloc == 'www.operanorth.co.uk' and parsed.path.startswith('/whats-on/'):
            urls.append(url)
    return list(dict.fromkeys(urls))


def resolve_city(venue):
    normalized = clean_text(venue).casefold()
    for marker, city in VENUE_CITIES.items():
        if marker.casefold() in normalized:
            return city
    return None


def parse_exact_date(value):
    text = clean_text(value)
    text = re.sub(r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+', '', text)
    text = re.sub(r'(?<=\d)(st|nd|rd|th)\b', '', text, flags=re.I)
    for pattern in ('%d %B %Y', '%d %b %Y'):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def parse_time(value):
    text = clean_text(value).lower().replace('.', ':').replace(' ', '')
    for pattern in ('%H:%M', '%I:%M%p', '%I%p'):
        try:
            return datetime.strptime(text, pattern).strftime('%H:%M')
        except ValueError:
            pass
    return None


def page_description(soup):
    parts = []
    subtitle = clean_text(soup.select_one('.c-single-event__subtitle'))
    overview = clean_text(soup.select_one('.c-single-event__lhs-info'))
    if subtitle:
        parts.append(subtitle)
    if overview:
        parts.append(overview)
    return '\n\n'.join(parts) or None


def base_record(title, event_date, time_from, venue, url, description):
    city = resolve_city(venue)
    if not title or not event_date or not venue or not city:
        return None
    return {
        'title': title,
        'date': event_date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'GB',
        'description': description,
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def performance_records(soup, url, title, description):
    records = []
    for row in soup.select('.c-event-instance'):
        date_node = row.select_one('time[datetime]')
        venue = clean_text(row.select_one('.c-event-instance__venue'))
        if not date_node:
            continue
        match = re.match(r'(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})', date_node.get('datetime', ''))
        if not match:
            continue
        try:
            event_date = datetime.strptime(match.group(1), '%Y-%m-%d').date().isoformat()
        except ValueError:
            continue
        record = base_record(
            title, event_date, f'{match.group(2)}:{match.group(3)}', venue, url, description
        )
        if record:
            records.append(record)
    return records


def single_performance_record(soup, url, title, description):
    event_date = parse_exact_date(soup.select_one('.c-single-event__daterange'))
    venue = clean_text(soup.select_one('.c-single-event__venue li'))
    time_from = None
    for paragraph in soup.select('.c-single-event__meat p'):
        strong = clean_text(paragraph.select_one('strong')).casefold()
        if strong == 'start time':
            value = clean_text(paragraph)
            value = re.sub(r'^start time\s*', '', value, flags=re.I)
            time_from = parse_time(value)
            break
    return base_record(title, event_date, time_from, venue, url, description)


def scrape_page(url):
    soup = BeautifulSoup(get_response(url).content, 'html.parser')
    title = re.sub(r'\s+', ' ', clean_text(soup.select_one('h1.c-single-event__title'))).strip()
    if not title:
        return []
    description = page_description(soup)
    records = performance_records(soup, url, title, description)
    if records:
        return records
    record = single_performance_record(soup, url, title, description)
    return [record] if record else []


def get_concerts():
    urls = event_urls()
    records = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(scrape_page, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                records.extend(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to scrape Opera North event',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
    unique_records = {
        (record['title'], record['date'], record['time_from'], record['venue']): record
        for record in records
    }
    return sorted(
        unique_records.values(),
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class OperaNorthCoUkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='operanorth_co_uk',
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
    OperaNorthCoUkCrawler().run()


if __name__ == '__main__':
    main()
