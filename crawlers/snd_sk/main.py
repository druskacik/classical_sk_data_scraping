import time
import datetime

import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts

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
        'event_type': event_type,
    }


def get_concert_data(url: str):
    """
    Get concert data from snd.sk
    """
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'lxml')
    events = []
    divs = soup.find_all('div', class_='calendar-events')
    for div in divs:
        events.extend(div.find_all('div', class_='performance'))
    data = []
    for event in events:
        data.append(extract_event_info(event))
    return data


def main():
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
    
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'time_to', 'event_type'])
    df = df[df['event_type'].isin(['opera', 'balet'])]
    df['url'] = df['url'].apply(lambda x: f'https://snd.sk{x}')
    df.insert(0, 'venue', 'Slovenské národné divadlo')
    df.insert(0, 'city', 'Bratislava')
    df.insert(0, 'source_url', 'https://snd.sk')
    df.insert(0, 'source', 'Slovenské národné divadlo')
    
    save_path = 'data/snd_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    # Rename 'event_type' key to 'type' in each dictionary
    for concert in concert_data:
        concert['type'] = concert.pop('event_type')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    upload_concerts(concert_data)

if __name__ == '__main__':
    main()


