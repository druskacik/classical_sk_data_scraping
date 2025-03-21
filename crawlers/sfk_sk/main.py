import datetime

import requests

import pandas as pd

from ..classical import upload_concerts

def validate_concert(concert):
    # Check if both start and end dates exist and if the difference is more than 1 day
    if concert.get('start') is not None and concert.get('end') is not None:
        start_time = datetime.datetime.fromisoformat(concert['start'].replace('Z', '+00:00'))
        end_time = datetime.datetime.fromisoformat(concert['end'].replace('Z', '+00:00'))
        
        # Calculate the difference in days
        time_difference = end_time - start_time
        
        # If the difference is more than 1 day, it's a festival or series, not a single concert
        if time_difference.days > 1:
            return False
    
    return True

def convert_date(date_str):
    """
    Convert date string to format 'yyyy-mm-dd'
    """
    if not isinstance(date_str, str):
        return None
    
    date = date_str.split(' ')[0]
    return f'{date.split('.')[2]}-{date.split('.')[1]}-{date.split('.')[0]}'

def extract_concert_info(concert):
    
    start_time = concert['start']
    # Parse the ISO format datetime
    datetime_obj = datetime.datetime.fromisoformat(start_time.replace('Z', '+00:00'))
    
    # Extract date in format dd.mm.yyyy
    date_str = datetime_obj.strftime('%d.%m.%Y')
    
    # Extract time in format HH:MM
    time_str = datetime_obj.strftime('%H:%M')
    
    return {
        'title': concert['title'],
        'date': convert_date(date_str),
        'url': 'https://www.sfk.sk' + concert['url'],
        'time_from': time_str,
        'time_to': None,
        'venue': concert['location'],
    }

def main():
    # Get current date and date one year from now
    current_date = datetime.date.today().strftime('%Y-%m-%d')
    end_date = (datetime.date.today() + datetime.timedelta(days=365)).strftime('%Y-%m-%d')
    url = f'https://www.sfk.sk/sk-sk/svc/rest/Event?start={current_date}&end={end_date}'
    r = requests.get(url, headers={ 'Accept': 'application/json' })
    
    r_json = r.json()
    i = 0
    concerts = []
    while True:
        concert = r_json.get(str(i))
        if concert is None:
            break
        if validate_concert(concert):
            concerts.append(extract_concert_info(concert))
        i += 1
    
    df = pd.DataFrame(concerts, columns=['title', 'date', 'url', 'time_from', 'time_to', 'venue'])
    df.insert(0, 'city', 'Košice')
    df.insert(0, 'source_url', 'https://www.sfk.sk')
    df.insert(0, 'source', 'Štátna filharmónia Košice')
    
    save_path = 'data/sfk_sk.csv'
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


