import requests
from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig

MONTH_TO_NUMBER = {
    'január': 1,
    'február': 2,
    'marec': 3,
    'apríl': 4,
    'máj': 5,
    'jún': 6,
    'júl': 7,
    'august': 8,
    'september': 9,
    'október': 10,
    'november': 11,
    'december': 12,
}

def extract_concerts(soup):
    article = soup.find('div', class_='article')
    ols = article.find_all('ol')
    concerts = []
    for ol in ols:
        date = ol.text.removeprefix('predstavenie: ')
        date_parts = date.split()
        year = date_parts[2].strip(',')
        month = MONTH_TO_NUMBER[date_parts[1]]
        day = date_parts[0].strip('.')
        time_from = date_parts[3]
        
        title = ol.next_sibling
        composers = title.next_sibling.text.split('–')
        composers = [c.strip() for c in composers]

        title = f'musica_litera: {title.text.strip()}'
        concerts.append({
            'title': title,
            'date': f'{year}-{month:02d}-{day}',
            'url': ol.find_next('a')['href'],
            'time_from': time_from,
            'composers': composers,
        })
    return concerts

class NedbalkaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='nedbalka_sk',
        source='Galéria Nedbalka',
        source_url='https://nedbalka.sk',
        columns=['title', 'date', 'url', 'time_from', 'composers'],
        front_fields=[
            ('venue', 'Galéria Nedbalka'),
            ('city', 'Bratislava'),
            ('source_url', 'https://nedbalka.sk'),
            ('source', 'Galéria Nedbalka'),
        ],
    )

    def scrape(self):
        url = 'https://www.nedbalka.sk/aktuality/koncerty-musica_litera/'
        r = requests.get(url)
        soup = BeautifulSoup(r.content, 'html.parser')
        return extract_concerts(soup)


def main():
    return NedbalkaCrawler().run()


if __name__ == '__main__':
    main()
