import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from observability import log_message


SOURCE_URL = 'https://www.mphil.de/'
CALENDAR_URL = urljoin(SOURCE_URL, 'kalender')
SOURCE = 'Münchner Philharmoniker'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en;q=0.7',
}

# The calendar includes the orchestra's tours. Most Munich venues do not put
# the city in their name, while touring venues generally do. Keeping this map
# explicit prevents a Munich default from leaking into touring performances.
VENUE_CITIES = {
    'Adrienne Arsht Center for the Performing Arts Miami': 'Miami',
    'Alte Oper Frankfurt': 'Frankfurt am Main',
    'Auditorio Nacional de Música Madrid': 'Madrid',
    'Basilika Ottobeuren': 'Ottobeuren',
    'Carnegie Hall New York': 'New York',
    'Concertgebouw Amsterdam': 'Amsterdam',
    'Elbphilharmonie Hamburg': 'Hamburg',
    'Hayes Hall Naples': 'Naples',
    'Konzerthaus Dortmund': 'Dortmund',
    'Kravis Center for the Performing Arts West Palm Beach': 'West Palm Beach',
    'Kultur- und Kongresszentrum Luzern': 'Luzern',
    'Lamstoahalle Frasdorf': 'Frasdorf',
    'Musikvereinssaal Wien': 'Wien',
    'Palais des Beaux-Arts Brüssel': 'Brüssel',
    'Palau de la Música Catalana Barcelona': 'Barcelona',
    'Philharmonie de Paris': 'Paris',
    'Royal Albert Hall London': 'London',
    'Steinmetz Hall Dr. Phillips Center for the Performing Arts Orlando': 'Orlando',
    'Symphony Hall Birmingham': 'Birmingham',
    'Teatro Galli Rimini': 'Rimini',
    'Wolkenturm Grafenegg': 'Grafenegg',
}

MUNICH_VENUES = {
    'Blitz',
    'Brainlab Firmenzentrale Riem',
    'Festsaal im Münchner Künstlerhaus',
    'Halle E',
    'Isarphilharmonie',
    'KULTURZENTRUM 2411 STADTTEILZENTRUM HASENBERGL-NORDHAIDE',
    'Kleiner Saal, Haus K',
    'Odeonsplatz',
    'Pacha',
    'Rote Sonne',
    'Saal X',
    'Stadtteilbibliothek Maxvorstadt',
    'Stadtteilbibliothek Sendling (Am Harras)',
}


def clean_text(value):
    if not value:
        return ''
    text = value.get_text('\n', strip=True) if hasattr(value, 'get_text') else str(value)
    text = text.replace('\xa0', ' ').replace('\u202f', ' ').replace('\u200b', '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def city_for_venue(venue):
    if venue in VENUE_CITIES:
        return VENUE_CITIES[venue]
    if venue == 'Haus Buchenried, Leoni am Starnberger See':
        return 'Berg'
    if venue in MUNICH_VENUES or 'Münchner' in venue:
        return 'München'
    return None


def description_from_item(item):
    parts = []
    topline = clean_text(item.select_one('.m-mphil-concertlist__topline'))
    if topline:
        parts.append(topline)

    people = []
    for person in item.select('.m-mphil-concertlist__person'):
        role = clean_text(person.select_one('.m-mphil-concertlist__instrument'))
        name = clean_text(person.select_one('.m-mphil-concertlist__person-link-title'))
        value = ' '.join(part for part in (role, name) if part)
        if value:
            people.append(value)
    if people:
        parts.append('Mitwirkende\n' + '\n'.join(people))

    works = [clean_text(work) for work in item.select('.m-mphil-concertlist__work-item')]
    works = [work for work in works if work]
    if works:
        parts.append('Programm\n' + '\n'.join(works))
    return '\n\n'.join(parts) or None


def parse_item(item):
    title = clean_text(item.select_one('.m-mphil-concertlist__title'))
    time_tag = item.select_one('time[datetime]')
    venue_node = item.select_one('.m-mphil-concertlist__venue')
    detail_link = item.select_one('a.m-mphil-concertlist__detail-link[href]')
    venue = clean_text(venue_node)
    city = city_for_venue(venue)
    if not title or not time_tag or not venue or not city or not detail_link:
        return None

    raw_datetime = time_tag.get('datetime', '').strip()
    try:
        start = datetime.strptime(raw_datetime, '%Y-%m-%d %H:%M')
    except ValueError:
        return None

    return {
        'title': title.replace('\n', ' – '),
        'date': start.date().isoformat(),
        'url': urljoin(SOURCE_URL, detail_link['href']),
        'time_from': start.strftime('%H:%M'),
        'venue': venue,
        'city': city,
        'country_code': 'DE',
        'description': description_from_item(item),
        'source_url': SOURCE_URL,
        'source': SOURCE,
    }


def get_concerts():
    response = requests.get(CALENDAR_URL, headers=HEADERS, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    records = []
    for item in soup.select('li.m-mphil-concertlist__item'):
        record = parse_item(item)
        if record:
            records.append(record)
        else:
            link = item.select_one('a.m-mphil-concertlist__detail-link[href]')
            log_message(
                'Skipped concert with incomplete date or location',
                event='crawler_item_skipped',
                level='warning',
                url=urljoin(SOURCE_URL, link['href']) if link else CALENDAR_URL,
            )
    return sorted(records, key=lambda row: (row['date'], row['time_from'], row['title']))


class MphilDeCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='mphil_de',
        source=SOURCE,
        source_url=SOURCE_URL,
        country_code='DE',
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
    MphilDeCrawler().run()


if __name__ == '__main__':
    main()
