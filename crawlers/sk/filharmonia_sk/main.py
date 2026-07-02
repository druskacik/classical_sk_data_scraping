import re
from datetime import date, datetime
import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig

def get_concerts():
    today = date.today()
    month_start = today.replace(day=1)
    url = f'https://www.filharmonia.sk/events-feed?start={month_start.isoformat()}'
    r = requests.get(url)
    concerts = r.json()
    concerts = [{
        'title': c['title'],
        'date': c['start'].split('T')[0],
        'time_from': c['start'].split('T')[1],
        'time_to': c['end'].split('T')[1],
        'url': f'https://filharmonia.sk{c["view_node"].removesuffix("/modal")}',
    } for c in concerts if datetime.fromisoformat(c['end']).date() >= today]
    return concerts

def get_concert_description(url):
    """
    Get concert description from filharmonia.sk
    """
    print(url)
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    div = soup.find('div', class_='region-content')
    text = div.get_text('\n').strip()
    # Clean up whitespace
    text = re.sub(r'\n+', '\n', text)
    return text

class FilharmoniaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='filharmonia_sk',
        source='Slovenská filharmónia',
        source_url='http://www.filharmonia.sk',
        columns=['title', 'date', 'time_from', 'time_to', 'url'],
        front_fields=[
            ('venue', 'Slovenská filharmónia'),
            ('city', 'Bratislava'),
            ('source_url', 'http://www.filharmonia.sk'),
            ('source', 'Slovenská filharmónia'),
        ],
    )

    def scrape(self):
        return get_concerts()

    def transform(self, df):
        df['description'] = df['url'].apply(get_concert_description)
        return df


def main():
    FilharmoniaCrawler().run()


if __name__ == '__main__':
    main()
