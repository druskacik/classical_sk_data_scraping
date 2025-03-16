import json
import datetime

import requests
from bs4 import BeautifulSoup

import pandas as pd

from ..classical import upload_concerts

def main():
    
    current_year = datetime.datetime.now().year
    current_month = datetime.datetime.now().month
    
    concert_jsons = []
    
    while True:
        # Find all script tags with type="application/ld+json"
        url = f'https://skozilina.sk/kalendar/month/{current_year}-{current_month:02d}'
        
        print(f'Getting concerts for {url} ...')
        r = requests.get(url)
        soup = BeautifulSoup(r.content, 'html.parser')
        script_tags = soup.find_all('script', type='application/ld+json')
        if len(script_tags) <= 1:
            break
        data = json.loads(script_tags[1].string)
        concert_jsons.extend(data)
        
        current_month += 1
        if current_month > 12:
            current_month = 1
            current_year += 1
    
    df = pd.DataFrame(concert_jsons)
    df = df[df['@type'] == 'Event'].copy()

    
    df['date'] = df['startDate'].apply(lambda x: x.split('T')[0])
    df['time_from'] = df['startDate'].apply(lambda x: x.split('T')[1])
    df['time_to'] = df['endDate'].apply(lambda x: x.split('T')[1])
    df['venue'] = df['location'].apply(lambda x: x['name'])
    df['city'] = df['location'].apply(lambda x: x['address'].get('addressLocality', 'Žilina'))
    
    df.rename(columns={
        'name': 'title',
    }, inplace=True)

    df.insert(0, 'source_url', 'https://skozilina.sk')
    df.insert(0, 'source', 'Štátny komorný orchester Žilina')
    
    save_path = 'data/skozilina_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    upload_concerts(concert_data)

if __name__ == '__main__':
    main()
    