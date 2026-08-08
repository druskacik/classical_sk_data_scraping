import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.tso.com.au/'
SITEMAP_URL = SOURCE_URL + 'sitemap.xml'
SOURCE = 'Tasmanian Symphony Orchestra'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'en-AU,en;q=0.9',
}


def clean_text(value):
    if value is None:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u200b', '').replace('\u200d', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def fetch(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def discover_event_urls(xml):
    soup = BeautifulSoup(xml, 'xml')
    urls = []
    for location in soup.find_all('loc'):
        url = clean_text(location)
        if re.fullmatch(r'/upcoming-concerts-and-events/[^/]+', urlparse(url).path.rstrip('/')):
            urls.append(url)
    if not urls:
        raise ValueError('No concert detail URLs were present in the sitemap')
    return list(dict.fromkeys(urls))


def parse_date(value):
    value = re.sub(
        r'^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+',
        '',
        value,
        flags=re.I,
    )
    for pattern in ('%d %b %Y', '%d %B %Y', '%d %b %y', '%d %B %y'):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def parse_time(value):
    match = re.fullmatch(r'\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap])m\s*', value, re.I)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    if match.group(3).lower() == 'p':
        hour += 12
    return f'{hour:02d}:{int(match.group(2) or 0):02d}'


def english_city(value):
    # The site respectfully uses the dual name "Nipaluna / Hobart". The
    # database city field uses the conventional English locality.
    return clean_text(value).rsplit('/', 1)[-1].strip()


def section_text(soup, heading_text):
    heading = next(
        (
            node for node in soup.find_all(['h2', 'strong'])
            if clean_text(node).casefold() == heading_text.casefold()
        ),
        None,
    )
    if not heading:
        return ''
    section = heading.find_parent('section')
    if not section:
        # Some Webflow sections use div wrappers around their CMS content.
        section = heading.parent.parent.parent
    return clean_text(section)


def description_for(soup, card):
    parts = []
    subtitle = clean_text(card.select_one('h1 + p'))
    if subtitle:
        parts.append(subtitle)
    for heading in ('Overview', 'About the music'):
        text = section_text(soup, heading)
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts) or None


def schedule_start_times(soup):
    text = section_text(soup, 'Schedule')
    values = re.findall(r'(\d{1,2}(?:[:.]\d{2})?\s*[ap]m)\s*\nStart\b', text, re.I)
    return list(dict.fromkeys(time for value in values if (time := parse_time(value))))


def parse_event_page(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    title_node = soup.find('h1')
    card = title_node.find_parent(class_='hero_card_content') if title_node else None
    if not card:
        return []

    info_items = card.select('.hero_card_info_item')
    if len(info_items) < 2:
        return []
    date_time = info_items[0].find_all('p', recursive=False)
    venue_city = info_items[1].find_all('p', recursive=False)
    if len(date_time) < 2 or len(venue_city) < 2:
        return []

    title = clean_text(title_node)
    event_date = parse_date(clean_text(date_time[0]))
    venue = clean_text(venue_city[0])
    city = english_city(venue_city[1])
    if not title or not event_date or not venue or not city:
        return []

    displayed_time = clean_text(date_time[1])
    times = schedule_start_times(soup) if displayed_time.casefold() == 'various times' else []
    times = times or [parse_time(displayed_time)]
    description = description_for(soup, card)

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
        }
        for time_from in times
    ]


class TsoComAuCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='tso_com_au',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='AU',
        upload_target='classical',
        columns=[
            'title', 'date', 'url', 'time_from', 'venue', 'city',
            'country_code', 'description',
        ],
        front_fields=[('source_url', SOURCE_URL), ('source', SOURCE)],
        dedupe_subset=['title', 'date', 'time_from', 'venue', 'city'],
    )

    def scrape(self):
        session = requests.Session()
        session.headers.update(HEADERS)
        sitemap = fetch(session, SITEMAP_URL)
        urls = discover_event_urls(sitemap.text)
        records = []

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch, session, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    response = future.result()
                    records.extend(parse_event_page(response.text, response.url))
                except requests.RequestException as error:
                    log_message(
                        'Failed to fetch TSO concert',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )

        return sorted(
            records,
            key=lambda item: (
                item['date'], item['time_from'] or '', item['title'], item['venue']
            ),
        )


def main():
    TsoComAuCrawler().run()


if __name__ == '__main__':
    main()
