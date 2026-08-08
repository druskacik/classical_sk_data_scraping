import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.stateopera.com.au/'
SOURCE = 'State Opera South Australia'
PRODUCTIONS_URL = urljoin(SOURCE_URL, 'productions')
ARCHIVE_URL = urljoin(SOURCE_URL, 'discover/past-productions')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}

DATE_FORMATS = ('%a, %d %b %Y', '%d %b %Y')


def clean_text(value):
    if value is None:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return BeautifulSoup(response.content, 'html.parser')


def discover_urls():
    urls = set()
    for page_url in (PRODUCTIONS_URL, ARCHIVE_URL):
        soup = fetch_soup(page_url)
        for link in soup.select('a[href*="/productions/"]'):
            href = link.get('href', '')
            if re.search(r'/productions/\d{4}/[^/?#]+', href):
                urls.add(urljoin(SOURCE_URL, href))
    return sorted(urls)


def parse_datetime(value):
    value = clean_text(value).replace('—', ' ').replace('–', ' ')
    time_match = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b', value, re.I)
    time_from = None
    if time_match:
        hour = int(time_match.group(1)) % 12
        if time_match.group(3).lower() == 'pm':
            hour += 12
        time_from = f'{hour:02d}:{int(time_match.group(2) or 0):02d}'
        value = value[:time_match.start()].strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat(), time_from
        except ValueError:
            pass
    return None, None


def city_for_venue(venue):
    key = clean_text(venue).lower()
    if not key or 'various location' in key:
        return None
    places = {
        'penola': 'Penola',
        'ukaria': 'Mount Barker Summit',
        'barossa arts centre': 'Tanunda',
        'bridgewater mill': 'Bridgewater',
        'glenelg beach': 'Glenelg',
        'plant 4 bowden': 'Bowden',
        'penfolds magill': 'Magill',
        'z ward': 'Glenside',
        'ridley centre': 'Wayville',
    }
    for marker, city in places.items():
        if marker in key:
            return city
    # The remaining named venues in this company's published history are in
    # Adelaide (including the CBD and immediately adjacent inner suburbs).
    adelaide_venues = (
        'festival theatre', "her majesty's theatre", 'opera theatre',
        'dunstan playhouse', 'adelaide town hall', 'state opera studio',
        'elder hall', 'sir samuel way', "st peter's cathedral",
        'memorial drive', 'victoria square', 'freemasons grand hall',
        'freemasons great hall', 'royalty theatre',
    )
    if any(marker in key for marker in adelaide_venues):
        return 'Adelaide'
    return None


def detail_value(soup, label):
    for section in soup.select('.production-info__section'):
        summary = section.find('summary')
        if clean_text(summary).lower() == label.lower():
            value = section.select_one('.production-info__value')
            return clean_text(value)
    return ''


def fallback_dates(soup):
    values = [clean_text(node) for node in soup.select('.hero__venue-value')]
    for value in values:
        match = re.search(
            r'(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s*[–—-]\s*'
            r'(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})', value,
        )
        if match:
            dates = [parse_datetime(match.group(1))[0], parse_datetime(match.group(2))[0]]
            return [(value, date, None) for date in dict.fromkeys(dates) if date]
        date_value, _ = parse_datetime(value)
        if date_value:
            return [(value, date_value, None)]
    return []


def parse_production(url, soup):
    title = clean_text(soup.select_one('h1.hero__title'))
    title = re.sub(r'\s*\(\d{4}\)\s*$', '', title).strip()
    venue = detail_value(soup, 'Venue')
    if not venue:
        hero_values = [clean_text(node) for node in soup.select('.hero__venue-value')]
        venue = next((value for value in hero_values if not re.search(r'\d{4}', value)), '')
    city = city_for_venue(venue)
    if not title or not venue or not city:
        return []

    composer = clean_text(soup.select_one('.hero__composer'))
    intro = clean_text(soup.select_one('.prod-intro'))
    description = '\n\n'.join(dict.fromkeys(part for part in (composer, intro) if part)) or None

    performances = []
    for option in soup.select('[data-perf-select] option:not([disabled])'):
        raw = clean_text(option)
        event_date, time_from = parse_datetime(raw)
        if event_date:
            performances.append((raw, event_date, time_from))
    if not performances:
        performances = fallback_dates(soup)

    return [{
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
    } for _, event_date, time_from in performances]


class StateoperaComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='stateopera_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description', 'source_url', 'source',
        ],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        urls = discover_urls()
        records = []
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(fetch_soup, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    records.extend(parse_production(url, future.result()))
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch State Opera production',
                        event='crawler_item_failed', level='warning', url=url,
                        error_type=type(error).__name__, error_message=str(error),
                    )
        return sorted(
            records,
            key=lambda item: (
                item['date'], item['time_from'] or '', item['title'], item['venue']
            ),
        )


def main():
    StateoperaComAuCrawler().run()


if __name__ == '__main__':
    main()
