import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig

URL = 'https://devin.stvr.sk/clanky/koncerty-live/388941/cyklus-organovych-koncertov-pod-pyramidou-januar-jun-2025'
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
}

def format_date(date):
    day, month = date.split('.')
    month = MONTHS_MAP[month.strip().lower()]
    return f'2025-{month}-{day}'

def extract_composer(line):
    if '(' in line and ')' in line:
        return line.split('(')[0].strip()
    return None

def extract_concerts(soup):
    body = soup.find('div', class_='article__body')
    ps = body.find_all('p')
    
    n_line = 0
    data = []
    concert = {}

    for p in ps:
        # This means its a new concert
        if p.attrs.get('align') == 'center':
            n_line = 1
            if concert:
                data.append(concert)
                concert = {}
            concert = {
                'date': p.text.strip(),
                'composers': [],
            }
        elif n_line > 0:
            if n_line == 1:
                interpreter = p.find('b').text.strip()
                concert['interpreter'] = interpreter
                for line in p.get_text(strip=True, separator='\n').splitlines():
                    composer = extract_composer(line)
                    if composer:
                        concert['composers'].append(composer)
            else:
                for line in p.get_text(strip=True, separator='\n').splitlines():
                    composer = extract_composer(line)
                    if composer:
                        concert['composers'].append(composer)
            n_line += 1
            
    if concert:
        data.append(concert)
        
    return data


class StvrCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='stvr_sk',
        source='STVR',
        source_url='https://devin.stvr.sk',
        columns=['date', 'interpreter', 'composers'],
        front_fields=[
            ('time_from', '10:30'),
            ('venue', 'Veľké koncertné štúdio Slovenského rozhlasu'),
            ('city', 'Bratislava'),
            ('url', URL),
            ('source_url', 'https://devin.stvr.sk'),
            ('source', 'STVR'),
        ],
    )

    def scrape(self):
        r = requests.get(URL)
        soup = BeautifulSoup(r.content, 'html.parser')
        return extract_concerts(soup)

    def transform(self, df):
        df['title'] = df['interpreter'].apply(lambda x: f'{BASE_NAME} - {x}')
        df['date'] = df['date'].apply(format_date)
        return df


def main():
    StvrCrawler().run()


if __name__ == '__main__':
    main()
