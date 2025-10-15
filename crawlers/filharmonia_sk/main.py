import re
from datetime import datetime
import requests

import pandas as pd
from bs4 import BeautifulSoup

from ..classical import upload_concerts

def get_concerts():
    today = str(datetime.today()).split()[0]
    url = f'https://www.filharmonia.sk/events-feed?start={today}'
    r = requests.get(url)
    concerts = r.json()
    concerts = [{
        'title': c['title'],
        'date': c['start'].split('T')[0],
        'time_from': c['start'].split('T')[1],
        'time_to': c['end'].split('T')[1],
        'url': f'https://filharmonia.sk{c["view_node"].removesuffix("/modal")}',
    } for c in concerts]
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

def main():
    print('Getting concerts for filharmonia.sk ...')
    concert_data = get_concerts()
    print(f'Found {len(concert_data)} concerts')
        
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'time_from', 'time_to', 'url'])
    df['description'] = df['url'].apply(get_concert_description)
    df.insert(0, 'venue', 'Slovenská filharmónia')
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


