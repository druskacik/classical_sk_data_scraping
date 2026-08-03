import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from observability import log_message

from ...base import BaseCrawler, CrawlerConfig


BASE_URL = 'https://www.fhk.cz/'
CALENDAR_URL = urljoin(BASE_URL, '123/Koncerty_chronologicky/')
CALENDAR_API_URL = urljoin(BASE_URL, 'common/action/tool/calendar/get_actions_in_cat/')
SOURCE = 'Filharmonie Hradec Králové'
DEFAULT_CITY = 'Hradec Králové'
COUNTRY_ONLY_LOCATIONS = {'Česká republika', 'Německo', 'Polsko', 'Rakousko', 'Slovensko', 'Švýcarsko'}


def clean_text(element):
    if element is None:
        return ''
    return ' '.join(element.get_text(' ', strip=True).split())


def discover_seasons(session):
    response = session.get(CALENDAR_URL, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    seasons = ['0']
    seasons.extend(
        button['data-season']
        for button in soup.select('[data-season]')
        if button.get('data-season')
    )
    max_month = '8'
    slider = soup.select_one('#calendar_action_month')
    if slider and slider.get('data-slider-max', '').isdigit():
        max_month = slider['data-slider-max']
    return list(dict.fromkeys(seasons)), max_month


def discover_event_urls(session):
    seasons, max_month = discover_seasons(session)
    urls = []
    headers = {'X-Requested-With': 'XMLHttpRequest', 'Referer': CALENDAR_URL}
    for season in seasons:
        data = [
            ('id_cats[]', '0'),
            ('months[]', '0'),
            ('months[]', max_month),
            ('places[]', '0'),
            ('seasons[]', season),
            ('cal_route', '/123/Koncerty_chronologicky/'),
        ]
        response = session.post(CALENDAR_API_URL, data=data, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.json()['content'], 'html.parser')
        for row in soup.select('.calendar_action_simple:not(.calendar_action_simple_others)'):
            link = row.select_one('a.btn-primary[href*="/calendar/"]')
            if link and link.get('href'):
                urls.append(urljoin(BASE_URL, link['href'].split('/back/')[0] + '/'))
    return list(dict.fromkeys(urls))


def parse_date(value):
    parts = value.strip().split('/')
    if len(parts) == 2:
        try:
            day, month = (int(part) for part in parts)
            today = datetime.now().date()
            year = today.year
            if today.month >= 7 and month < 7:
                year += 1
            elif today.month < 7 and month >= 7:
                year -= 1
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None
    if len(parts) != 3:
        return None
    try:
        return datetime.strptime(value.strip(), '%d/%m/%Y').date().isoformat()
    except ValueError:
        return None


def split_location(value):
    value = value.strip(' ,')
    if not value:
        return None, None
    if ',' in value:
        city, venue = (part.strip() for part in value.split(',', 1))
        # Some tour entries contain only "country, city" and provide no venue.
        if city in COUNTRY_ONLY_LOCATIONS:
            return None, None
        return city or None, venue or None
    return DEFAULT_CITY, value


def parse_event_page(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    row = soup.select_one('.calendar_action_simple')
    if row is None:
        return None

    title = clean_text(row.select_one('.calendar_action_simple_main_info h3'))
    date = parse_date(clean_text(row.select_one('.calendar_recomended_date')))
    date_info = clean_text(row.select_one('.action_simple_date .calendar_recomended_info'))
    time_match = re.search(r'\b([01]?\d|2[0-3]):[0-5]\d\b', date_info)
    time_from = time_match.group(0) if time_match else None

    info = row.select_one('.calendar_action_simple_main_info .calendar_recomended_info')
    category = info.select_one('.calendar_recomended_cat') if info else None
    if category:
        category.extract()
    city, venue = split_location(clean_text(info))

    description = clean_text(soup.select_one('.calendar_action_complete_info_block')) or None
    if not all((title, date, city, venue)):
        log_message(
            'Skipping event with incomplete required fields',
            event='crawler_item_skipped',
            level='warning',
            url=url,
        )
        return None
    return {
        'title': title,
        'date': date,
        'url': url,
        'time_from': time_from,
        'venue': venue,
        'city': city,
        'country_code': 'CZ',
        'description': description,
        'source_url': BASE_URL,
        'source': SOURCE,
    }


def fetch_event(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return parse_event_page(response.text, url)


class FhkCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='fhk_cz',
        source=SOURCE,
        source_url=BASE_URL,
        country_code='CZ',
        upload_target='classical',
        dedupe_subset=['url', 'date'],
    )

    def scrape(self):
        with requests.Session() as session:
            urls = discover_event_urls(session)

        records = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_event, url): url for url in urls}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    record = future.result()
                    if record:
                        records.append(record)
                except Exception as error:
                    log_message(
                        'Failed to scrape concert detail',
                        event='crawler_item_failed',
                        level='warning',
                        url=url,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
        return sorted(records, key=lambda record: (record['date'], record['time_from'] or '', record['title']))


def main():
    FhkCrawler().run()


if __name__ == '__main__':
    main()
