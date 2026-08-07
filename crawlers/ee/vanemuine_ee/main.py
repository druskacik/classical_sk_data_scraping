import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://vanemuine.ee/'
PROGRAM_URL = urljoin(SOURCE_URL, 'mangukava/')
SOURCE = 'Vanemuine'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'et-EE,et;q=0.9,en;q=0.7',
}

# The schedule's playing-place labels are authoritative.  Most performances
# are in Tartu, but Vanemuine also publishes tours around Estonia and Latvia.
LOCATIONS = {
    'Väikeses majas': ('Vanemuise väike maja', 'Tartu', 'EE'),
    'Suures majas': ('Vanemuise suur maja', 'Tartu', 'EE'),
    'Sadamateatris': ('Sadamateater', 'Tartu', 'EE'),
    'Vanemuise Kontserdimajas': ('Vanemuise kontserdimaja', 'Tartu', 'EE'),
    'Vanemuise suures majas ja Vanemuise Kontserdimajas': (
        'Vanemuise suur maja ja Vanemuise kontserdimaja', 'Tartu', 'EE'
    ),
    'Heino Elleri Muusikakooli Tubina saalis': (
        'Heino Elleri Muusikakooli Tubina saal', 'Tartu', 'EE'
    ),
    'Tartu Pauluse kirik': ('Tartu Pauluse kirik', 'Tartu', 'EE'),
    'Eesti Noorsooteatris': ('Eesti Noorsooteater', 'Tallinn', 'EE'),
    'Alexela Kontserdimajas': ('Alexela kontserdimaja', 'Tallinn', 'EE'),
    'Salme Kultuurikeskuses': ('Salme kultuurikeskus', 'Tallinn', 'EE'),
    'Südalinna Teater': ('Südalinna Teater', 'Tallinn', 'EE'),
    'Viimsi Artium': ('Viimsi Artium', 'Viimsi', 'EE'),
    'Haapsalu Kultuurikeskuses': ('Haapsalu kultuurikeskus', 'Haapsalu', 'EE'),
    'Endla teatris': ('Endla teater', 'Pärnu', 'EE'),
    'Rakvere Teatris': ('Rakvere Teater', 'Rakvere', 'EE'),
    'Arvo Pärdile pühendatud muusikamaja Ukuaru (Rakvere)': (
        'Muusikamaja Ukuaru', 'Rakvere', 'EE'
    ),
    'Valga Kultuurikeskuses': ('Valga kultuurikeskus', 'Valga', 'EE'),
    'Võru Kultuurimajas Kannel': ('Võru kultuurimaja Kannel', 'Võru', 'EE'),
    'Jõhvi Kontserdimajas': ('Jõhvi kontserdimaja', 'Jõhvi', 'EE'),
    'Kuressaare teatris': ('Kuressaare Teater', 'Kuressaare', 'EE'),
    'Kuressaare spordikeskuses (Vallimaa 16A)': (
        'Kuressaare spordikeskus', 'Kuressaare', 'EE'
    ),
    'Saaremaa ooperipäevad': ('Saaremaa ooperipäevad', 'Kuressaare', 'EE'),
    'Paide Muusika- ja Teatrimajas': ('Paide Muusika- ja Teatrimaja', 'Paide', 'EE'),
    'Dailes Teater': ('Dailes Teātris', 'Riga', 'LV'),
    'Läti Rahvusooperis': ('Latvian National Opera', 'Riga', 'LV'),
}


def clean_text(value):
    if not value:
        return ''
    text = str(value).replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def node_text(parent, selector, separator=' '):
    node = parent.select_one(selector)
    return clean_text(node.get_text(separator, strip=True)) if node else ''


def parse_date(value):
    try:
        return datetime.strptime(value, '%m/%d/%Y').date().isoformat()
    except (TypeError, ValueError):
        return None


def parse_time(value):
    match = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', value or '')
    return f'{int(match.group(1)):02d}:{match.group(2)}' if match else None


def parse_listing(soup):
    records = []
    for item in soup.select('.repertoire-item'):
        title = node_text(item, '.item__content p')
        date = parse_date(item.get('data-filter-date'))
        link = item.select_one('.item__content p a[href]')
        url = urljoin(PROGRAM_URL, link.get('href')) if link else ''
        location_label = node_text(item, '.item__location')
        location = LOCATIONS.get(location_label)

        if not title or not date or not url or not location:
            if location_label and not location:
                log_message(
                    'Skipping event with an unknown playing place',
                    event='crawler_item_skipped',
                    level='warning',
                    url=url or PROGRAM_URL,
                    venue=location_label,
                )
            continue

        venue, city, country_code = location
        records.append({
            'title': title,
            'date': date,
            'url': url,
            'time_from': parse_time(node_text(item, '.item__date .time')),
            'venue': venue,
            'city': city,
            'country_code': country_code,
            'description': None,
            'source_url': SOURCE_URL,
            'source': SOURCE,
        })
    return records


def parse_description(html):
    soup = BeautifulSoup(html, 'html.parser')
    # This block contains the synopsis, programme, credits, and cast, while
    # excluding the occurrence carousel, gallery, ticket UI, and footer.
    block = soup.select_one('main .ama-block.block-narrow .block__body')
    return clean_text(block.get_text('\n', strip=True)) or None if block else None


def get_page(session, url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.text


def get_concerts():
    session = requests.Session()
    session.headers.update(HEADERS)
    listing_html = get_page(session, PROGRAM_URL)
    records = parse_listing(BeautifulSoup(listing_html, 'html.parser'))

    descriptions = {}
    urls = sorted({record['url'] for record in records})
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_page, session, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                descriptions[url] = parse_description(future.result())
            except requests.RequestException as error:
                log_message(
                    'Failed to fetch Vanemuine production details',
                    event='crawler_item_failed',
                    level='warning',
                    url=url,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )

    for record in records:
        record['description'] = descriptions.get(record['url'])

    return sorted(
        records,
        key=lambda record: (
            record['date'], record['time_from'] or '', record['title'], record['venue']
        ),
    )


class VanemuineEeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='vanemuine_ee',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='EE',
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
        return get_concerts()


def main():
    VanemuineEeCrawler().run()


if __name__ == '__main__':
    main()
