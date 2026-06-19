import json
import html
import datetime

import requests
from bs4 import BeautifulSoup

import pandas as pd

from ...base import BaseCrawler, CrawlerConfig

def extract_description(url):
    print(url)
    r = requests.get(url)
    soup = BeautifulSoup(r.text, 'html.parser')
    program = soup.find('div', class_='program')
    return program.text.strip()

class SkozilinaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='skozilina_sk',
        source='Štátny komorný orchester Žilina',
        source_url='https://skozilina.sk',
        front_fields=[
            ('source_url', 'https://skozilina.sk'),
            ('source', 'Štátny komorný orchester Žilina'),
        ],
    )

    def scrape(self):
        current_year = datetime.datetime.now().year
        current_month = datetime.datetime.now().month

        concert_jsons = []

        while True:
            url = f'https://skozilina.sk/kalendar/month/{current_year}-{current_month:02d}'

            print(f'Getting concerts for {url} ...')
            r = requests.get(url)
            soup = BeautifulSoup(r.content, 'html.parser')
            script_tags = soup.find_all('script', type='application/ld+json')
            if len(script_tags) <= 1:
                break
            data = json.loads(html.unescape(script_tags[1].string))
            concert_jsons.extend(data)

            current_month += 1
            if current_month > 12:
                current_month = 1
                current_year += 1

        return concert_jsons

    def transform(self, df):
        df = df[df['@type'] == 'Event'].copy()
        df = df[df.location.notna()].copy()

        df['date'] = df['startDate'].apply(lambda x: x.split('T')[0])
        df['time_from'] = df['startDate'].apply(lambda x: x.split('T')[1])
        df['time_to'] = df['endDate'].apply(lambda x: x.split('T')[1])
        df['venue'] = df['location'].apply(lambda x: x['name'] if not pd.isna(x) else None)
        df['city'] = df['location'].apply(lambda x: x['address'].get('addressLocality', 'Žilina'))

        df.rename(columns={
            'name': 'title',
        }, inplace=True)

        df.drop_duplicates(subset=['title', 'date', 'url'], inplace=True)
        df['description'] = df['url'].apply(extract_description)
        return df


def main():
    SkozilinaCrawler().run()


if __name__ == '__main__':
    main()
