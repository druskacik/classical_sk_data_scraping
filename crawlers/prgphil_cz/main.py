import re

import requests
from bs4 import BeautifulSoup

from ..base import BaseCrawler, CrawlerConfig


def parse_date(date_str):
    """Convert 'dd. mm. yy' to 'yyyy-mm-dd'."""
    parts = date_str.strip().replace('.', '').split()
    day, month, year = parts[0], parts[1], parts[2]
    year = f'20{year}' if len(year) == 2 else year
    return f'{year}-{month.zfill(2)}-{day.zfill(2)}'


def parse_time(date2_str):
    """Extract 'HH:MM' from 'Út • 19:30'."""
    match = re.search(r'(\d{1,2}:\d{2})', date2_str)
    return match.group(1) if match else ''


def extract_concert(card):
    header = card.find('a', class_='vypis-ko-hlavicka')
    if not header:
        return None

    url = header.get('href', '')
    if url and not url.startswith('http'):
        url = f'https://www.prgphil.cz{url}'

    date1 = card.find('div', class_='date-1')
    date2 = card.find('div', class_='date-2')
    date = parse_date(date1.text) if date1 else ''
    time_from = parse_time(date2.text) if date2 else ''

    venue_el = card.select_one('.field--name-name')
    venue = venue_el.text.strip() if venue_el else ''

    title_el = card.select_one('h3.nadpis span')
    if not title_el:
        title_el = card.select_one('h3.nadpis')
    title = title_el.text.strip() if title_el else ''

    cycle_el = card.find('span', class_='vypis-ko-cyklus')
    cycle = cycle_el.text.strip() if cycle_el else ''

    # Extract performers as description
    performers = []
    for p in card.select('.paragraph--interpret'):
        name_el = p.select_one('.field--name-field-interpret')
        role_el = p.select_one('.field--name-field-nastroj')
        name = name_el.text.strip() if name_el else ''
        role = role_el.text.strip() if role_el else ''
        if name:
            performers.append(f'{name} — {role}' if role else name)

    description = f'{cycle}\n{chr(10).join(performers)}' if performers else cycle

    return {
        'title': title,
        'date': date,
        'time_from': time_from,
        'url': url,
        'venue': venue,
        'type': cycle,
        'description': description,
    }


def get_concerts():
    r = requests.get('https://www.prgphil.cz/koncerty-a-vstupenky')
    soup = BeautifulSoup(r.content, 'html.parser')

    cards = soup.select('div.vypis-ko-koncert')
    concerts = []
    for card in cards:
        concert = extract_concert(card)
        if concert and concert['title']:
            concerts.append(concert)

    return concerts


class PrgphilCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='prgphil_cz',
        source='Prague Philharmonia',
        source_url='https://www.prgphil.cz',
        columns=['title', 'date', 'time_from', 'url', 'venue', 'type', 'description'],
        front_fields=[
            ('source_url', 'https://www.prgphil.cz'),
            ('source', 'Prague Philharmonia'),
            ('city', 'Praha'),
        ],
    )

    def scrape(self):
        return get_concerts()


def main():
    PrgphilCrawler().run()


if __name__ == '__main__':
    main()
