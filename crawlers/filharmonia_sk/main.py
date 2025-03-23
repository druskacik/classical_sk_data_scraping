import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts

MONTHS_MAPPING = {
    'Jan.': '01',
    'Feb.': '02',
    'Mar.': '03',
    'Apr.': '04',
    'Máj.': '05',
    'Jún.': '06',
    'Júl.': '07',
    'Aug.': '08',
    'Sep.': '09',
    'Okt.': '10',
    'Nov.': '11',
    'Dec.': '12',
}

def convert_date(date_str):
    """
    Convert date string to format 'yyyy-mm-dd'
    """
    day = date_str.split(' ')[0].strip('.')
    month = MONTHS_MAPPING[date_str.split(' ')[1]]
    year = date_str.split(' ')[2].strip(',')
    return f'{year}-{month}-{day}'

def convert_time(date_str):
    """
    Convert time string to format 'HH:MM'
    """
    time = date_str.split(' ')[3]
    if time == '00:00':
        return None
    return time

def get_concert_data():
    """
    Get concert data from filharmonia.sk
    """
    url = 'http://www.filharmonia.sk/o-nas/archiv-slovenskej-filharmonie/sezona-2024-2025'
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'lxml')
    concerts = soup.find_all('li', class_='koncerty')
    data = []
    for c in concerts:
        date = c.find('span', class_='date').text
        data.append({
            'title': c.find('a', class_='title').text,
            'url': c.find('a', class_='title')['href'],
            'date': convert_date(date),
            'venue': c.find('a', class_='venue').text,
            'time_from': convert_time(date),
        })
    return data

def get_concert_description(url):
    """
    Get concert description from filharmonia.sk
    """
    print(url)
    r = requests.get(url)
    soup = BeautifulSoup(r.content, 'lxml')
    article = soup.find('article')
    return article.text.strip()

def main():
    print('Getting concerts for filharmonia.sk ...')
    concert_data = get_concert_data()
    print(f'Found {len(concert_data)} concerts')
        
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'venue'])   
    df['description'] = df['url'].apply(get_concert_description)
    df.insert(0, 'city', 'Bratislava')
    df.insert(0, 'source_url', 'http://www.filharmonia.sk')
    df.insert(0, 'source', 'Slovenská filharmónia')
    
    save_path = 'data/filharmonia_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    inserted_count, skipped_count = upload_concerts(concert_data)
    print(f'Uploaded {inserted_count} concerts, skipped {skipped_count} concerts')
    
if __name__ == '__main__':
    main()


