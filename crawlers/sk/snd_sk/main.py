import time
import datetime
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig

def convert_date(date_str):
    """
    Convert date string of format '01.12.2025' to '2025-12-01'
    """
    day, month, year = date_str.split('.')
    return f'{year}-{month}-{day}'

def convert_time(time_str):
    """
    Convert time string of format '19.00 h' to '19:00'
    """
    return time_str[:5].replace('.', ':')
    

def extract_event_info(event):
    title = event.find('div', class_='title').find('span', class_='value').text
    date = event.find('div', class_='date').find('span', class_='on-date').text
    url = event.find('div', class_='detail-link').find('a')['href']
    time_from = event.find('div', class_='date').find('span', class_='time-from').text
    time_to = event.find('div', class_='date').find('span', class_='time-to').text
    event_type = event.find('div', class_='artistic-body').find('span', class_='value').text
    return {
        'title': title,
        'date': convert_date(date),
        'url': url,
        'time_from': convert_time(time_from),
        'time_to': convert_time(time_to),
        'type': event_type,
    }


def get_concert_data(url: str):
    """
    Get concert data from snd.sk
    """
    r = requests.get(url, verify=False)
    soup = BeautifulSoup(r.content, 'lxml')
    events = []
    divs = soup.find_all('div', class_='calendar-events')
    for div in divs:
        events.extend(div.find_all('div', class_='performance'))
    data = []
    for event in events:
        data.append(extract_event_info(event))
    return data

def extract_description(url):
    print(url)
    r = requests.get(url, verify=False, timeout=20)
    soup = BeautifulSoup(r.text, 'html.parser')
    description = soup.find('meta', attrs={'property': 'og:description'})
    if description and description.get('content'):
        return description.get('content').strip()
    content = soup.find('div', class_='content') or soup.find('main')
    if content:
        return content.get_text('\n', strip=True)
    return None


class SndCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='snd_sk',
        source='Slovenské národné divadlo',
        source_url='https://snd.sk',
        columns=['title', 'date', 'url', 'time_from', 'time_to', 'type'],
        front_fields=[
            ('venue', 'Slovenské národné divadlo'),
            ('city', 'Bratislava'),
            ('source_url', 'https://snd.sk'),
            ('source', 'Slovenské národné divadlo'),
        ],
    )

    def scrape(self):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        base_url = 'https://snd.sk/program/'
        current_season_year = datetime.datetime.now().year
        current_month = datetime.datetime.now().month

        concert_data = []

        if current_month <= 6:
            current_season_year = current_season_year - 1
            for month in range(current_month, 6 + 1):
                url = f'{base_url}{current_season_year}-{current_season_year+1}/{month:02d}'
                print(f'Getting concerts for {url} ...')
                concert_data.extend(get_concert_data(url))
        else:
            for month in range(current_month, 12 + 1):
                url = f'{base_url}{current_season_year}-{current_season_year+1}/{month:02d}'
                print(f'Getting concerts for {url} ...')
                concert_data.extend(get_concert_data(url))
            for month in range(1, 6 + 1):
                url = f'{base_url}{current_season_year+1}-{current_season_year+2}/{month:02d}'
                print(f'Getting concerts for {url} ...')
                concert_data.extend(get_concert_data(url))

        return concert_data

    def transform(self, df):
        df = df[df['type'].isin(['opera', 'balet'])].copy()
        df['url'] = df['url'].apply(lambda x: f'https://snd.sk{x}')
        with ThreadPoolExecutor(max_workers=8) as executor:
            df['description'] = list(executor.map(extract_description, df['url']))
        return df


def main():
    SndCrawler().run()


if __name__ == '__main__':
    main()
