import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts

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

def main():
    print('Getting concerts for stateopera.sk ...')
    url = 'https://www.stateopera.sk/sk/program?filter=0'
    
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'html.parser')
    
    concert_divs = soup.find_all('div', attrs={'data-filter': True})
    concert_data = [extract_concert_info(div) for div in concert_divs]
    
    print(f'Found {len(concert_data)} concerts')
        
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'type'])
    df.insert(0, 'venue', None)
    df.insert(0, 'city', 'Banská Bystrica')
    df.insert(0, 'source_url', 'https://www.stateopera.sk')
    df.insert(0, 'source', 'Štátna opera')
    
    save_path = 'data/stateopera_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    upload_concerts(concert_data)
    
if __name__ == '__main__':
    main()
