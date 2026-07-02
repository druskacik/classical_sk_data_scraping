import re
from datetime import date as date_cls

import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig

URL = 'https://devin.stvr.sk/clanky/koncerty-live'
BASE_NAME = 'Cyklus Organových koncertov pod pyramídou'

MONTHS_MAP = {
    'január': '01',
    'február': '02',
    'marec': '03',
    'apríl': '04',
    'máj': '05',
    'jún': '06',
    'júl': '07',
    'august': '08',
    'september': '09',
    'október': '10',
    'november': '11',
	'december': '12',
    'januára': '01',
    'februára': '02',
    'marca': '03',
    'apríla': '04',
    'mája': '05',
    'júna': '06',
    'júla': '07',
    'augusta': '08',
    'septembra': '09',
    'októbra': '10',
    'novembra': '11',
    'decembra': '12',
}

def format_date(date_text, year):
    match = re.search(r'(\d{1,2})\.\s*([a-záäčďéíĺľňóôŕšťúýž]+)', date_text, re.IGNORECASE)
    if not match:
        return None
    day = int(match.group(1))
    month = MONTHS_MAP[match.group(2).lower()]
    return f'{year}-{month}-{day:02d}'

def extract_composer(line):
    if '(' in line and ')' in line:
        return line.split('(')[0].strip()
    return None

def extract_year(text):
    match = re.search(r'\b(20\d{2})\b', text)
    if match:
        return int(match.group(1))
    return date_cls.today().year

def extract_article_links(soup):
    links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        title = a.get_text(' ', strip=True)
        if 'cyklus-organovych-koncertov-pod-pyramidou' not in href:
            continue
        if href.startswith('/'):
            href = f'https://devin.stvr.sk{href}'
        if href not in links:
            links.append(href)
    return links

def extract_concert(soup, url):
    body = soup.find('div', class_='article__body')
    if body is None:
        return None

    text = body.get_text('\n', strip=True)
    year = extract_year(soup.get_text(' ', strip=True))
    date_match = re.search(r'\d{1,2}\.\s*[a-záäčďéíĺľňóôŕšťúýž]+', text, re.IGNORECASE)
    if date_match is None:
        return None
    date = format_date(date_match.group(0), year)
    if date is None or date_cls.fromisoformat(date) < date_cls.today():
        return None

    title = soup.find('h1').get_text(' ', strip=True)
    interpreter = title.split(':', 1)[1].strip() if ':' in title else title
    composers = []
    for line in text.splitlines():
        composer = extract_composer(line)
        if composer:
            composers.append(composer)

    return {
        'date': date,
        'interpreter': interpreter,
        'composers': list(dict.fromkeys(composers)),
        'url': url,
        'description': text,
    }


class StvrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='stvr_sk',
        source='STVR',
        source_url='https://devin.stvr.sk',
        columns=['date', 'interpreter', 'composers', 'url', 'description'],
        front_fields=[
            ('time_from', '10:30'),
            ('venue', 'Veľké koncertné štúdio Slovenského rozhlasu'),
            ('city', 'Bratislava'),
            ('source_url', 'https://devin.stvr.sk'),
            ('source', 'STVR'),
        ],
    )

    def scrape(self):
        r = requests.get(URL)
        soup = BeautifulSoup(r.content, 'html.parser')
        concerts = []
        for url in extract_article_links(soup):
            r = requests.get(url)
            article_soup = BeautifulSoup(r.content, 'html.parser')
            concert = extract_concert(article_soup, url)
            if concert is not None:
                concerts.append(concert)
        return concerts

    def transform(self, df):
        df['title'] = df['interpreter'].apply(lambda x: f'{BASE_NAME} - {x}')
        return df


def main():
    StvrCrawler().run()


if __name__ == '__main__':
    main()
