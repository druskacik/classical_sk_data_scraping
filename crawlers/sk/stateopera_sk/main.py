import requests

from bs4 import BeautifulSoup

from ...base import BaseCrawler, CrawlerConfig
from ...formaters import clean_string

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

def extract_concert_info(concert):
    
    title = concert.find('h3', attrs={'class': 'title'}).text.strip()
    event_type = concert.find('span', attrs={'data-ctg': True}).text.strip()
    day = concert.find('div', attrs={'class': 'day'}).text.strip()
    month = concert.find('div', attrs={'class': 'month'}).text.strip()
    year = concert.find('div', attrs={'class': 'year'}).text.strip()
    time = concert.find('div', attrs={'class': 'timeslot'}).text.strip().split()[1]
    
    href = concert.find('a').get('href')
    url = f'https://www.stateopera.sk/{href}' 
    
    month = MONTH_TO_NUMBER[month.lower()]
    
    return {
        'title': title,
        'date': f'{year}-{month:02d}-{day}',
        'url': url,
        'time_from': time,
        'type': event_type,
	}
    
def extract_description(url):
    print(url)
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    description = soup.find('div', class_='longtext').text.strip()
    description = clean_string(description)
    return description

class StateOperaCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='stateopera_sk',
        source='Štátna opera',
        source_url='https://www.stateopera.sk',
        columns=['title', 'date', 'url', 'time_from', 'type'],
        front_fields=[
            ('venue', None),
            ('city', 'Banská Bystrica'),
            ('source_url', 'https://www.stateopera.sk'),
            ('source', 'Štátna opera'),
        ],
    )

    def scrape(self):
        url = 'https://www.stateopera.sk/sk/program?filter=0'
        r = requests.get(url)
        soup = BeautifulSoup(r.content, 'html.parser')

        concert_divs = soup.find_all('div', attrs={'data-filter': True})
        return [extract_concert_info(div) for div in concert_divs]

    def transform(self, df):
        df['description'] = df['url'].apply(extract_description)
        return df


def main():
    StateOperaCrawler().run()


if __name__ == '__main__':
    main()
