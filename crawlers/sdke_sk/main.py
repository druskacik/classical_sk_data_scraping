from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

import pandas as pd

from ..classical import upload_concerts

def extract_data_ids():
    current_month = datetime.now().strftime('%Y%m')
    base_url = 'https://www.sdke.sk/sk/divadlo/program/'
    
    data_id_values = []
    
    while True:
        url = f"{base_url}{current_month}"
        print(url)
        r = requests.get(url)
        soup = BeautifulSoup(r.content, 'html.parser')
        spans_with_data_id = soup.find_all('span', attrs={'data-id': True})
        if len(spans_with_data_id) == 0:
            break
        data_id_values.extend([span['data-id'] for span in spans_with_data_id])
        current_month = datetime.strptime(current_month, '%Y%m')
        current_month = current_month + timedelta(days=31)
        current_month = current_month.strftime('%Y%m')
        
    return list(set(data_id_values))

def extract_concert_data(data_id):
    url = f"https://www.sdke.sk/sk/api/{data_id}"
    print(url)
    r = requests.get(url)
    return r.json()

def extract_type(path):
    type_raw = path.split('/')[2]
    types_map = {
        'cinohra': 'Činohra',
        'balet': 'Balet',
        'opera': 'Opera',
        'host': 'Hosť',
        'divadlo': 'Divadlo',
	}
    return types_map[type_raw]

def extract_date_and_time(date_str):
    """
    Extract date and time from a string like 'štvrtok 20. marec 2025 - 10:00'
    
    Args:
        date_str (str): Date string in format 'day_of_week day. month year - time'
    
    Returns:
        tuple: (date, time) where date is a datetime object and time is a string
    """
    # Split the date and time parts
    date_part, time_part = date_str.split(' - ')
    
    # Map Slovak month names to numbers
    month_map = {
        'január': 1, 'február': 2, 'marec': 3, 'apríl': 4, 'máj': 5, 'jún': 6,
        'júl': 7, 'august': 8, 'september': 9, 'október': 10, 'november': 11, 'december': 12,
    }
    
    # Split the date part into components
    date_components = date_part.split()
    
    # Extract day, month, and year
    day_of_week = date_components[0]  # Not used in datetime conversion but available
    day = int(date_components[1].strip('.'))
    month_name = date_components[2]
    month = month_map[month_name]
    year = int(date_components[3])
    
    # Create datetime object
    date_obj = datetime(year, month, day)
    
    return date_obj, time_part


def main():
    data_ids = extract_data_ids()
    concert_data = []

    for data_id in data_ids:
        concert = extract_concert_data(data_id)
        for i, obj in enumerate(concert):
            date = obj['dates'].split('|')[i]
            date_obj, time_part = extract_date_and_time(date)
            concert_data.append({
                **obj,
                'url': f"https://www.sdke.sk{obj['path']}",
                'date': date_obj.strftime('%Y/%m/%d'),
                'time_from': time_part,
                'link': obj['links'].split('|')[i],
                'type': extract_type(obj['path']),
            })

        
    df = pd.DataFrame(concert_data, columns=['title', 'date', 'url', 'time_from', 'type'])
    df = df[df['type'].isin(['Balet', 'Opera'])].reset_index(drop=True)
    df.drop_duplicates(subset=['title', 'date', 'url'], inplace=True)
    
    df.insert(0, 'city', 'Košice')
    df.insert(0, 'venue', 'Národné divadlo Košice')
    df.insert(0, 'source_url', 'https://www.sdke.sk')
    df.insert(0, 'source', 'Národné divadlo Košice')
    
    save_path = 'data/sdke_sk.csv'
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
