import requests

import pandas as pd

from ..classical import upload_concerts

def validate_concert(concert):
    if concert['Event']['IsSeasonTicketEvent']:
        return False
    return True

def extract_concert_info(concert):
    event = concert['Event']
    return {
        'title': event['ProfileName'],
		'venue': event['Building']['ProfileName'],
		'city': event['AddressContact']['City'],
		'time_from': event['Begin'].split('T')[1][:5],
		'time_to': event['End'].split('T')[1][:5],
		'date': event['Begin'].split('T')[0].replace('-', '/'),
		'url': f"https://tootoot.fm/sk/events/{event['_id']}"
    }

def main():
    print('Getting concerts for tootoot.fm ...')
    url = 'https://api.tootoot.co/api/event/search?categories=548057368d4031089cea31f6&cityId=&page=0&perPage=99'
    
    r = requests.get(url)
    concerts = r.json()
    concerts = [c for c in concerts if validate_concert(c)]
    concert_data = [extract_concert_info(concert) for concert in concerts]
    
    print(f'Found {len(concert_data)} concerts')
        
    df = pd.DataFrame(concert_data, columns=['title', 'venue', 'city', 'time_from', 'time_to', 'date', 'url'])
    df.insert(0, 'source_url', 'https://tootoot.fm')
    df.insert(0, 'source', 'tootoot')
    
    df.drop_duplicates(subset=['title', 'date', 'time_from'], inplace=True)
    
    save_path = 'data/tootoot_fm.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    upload_concerts(concert_data)
    
if __name__ == '__main__':
    main()
