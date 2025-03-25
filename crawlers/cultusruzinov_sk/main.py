import random
import requests

import pandas as pd

from ..classical import upload_potential_concerts

def get_access_token():
    url = 'https://www.cultusruzinov.sk/_api/v1/access-tokens'
    r = requests.get(url)
    response = r.json()
    key = random.choice(list(response['apps'].keys()))
    return response['apps'][key]['instance']

def get_event_slugs(access_token):
    url = 'https://www.cultusruzinov.sk/_api/wix-one-events-server/web/paginated-events/viewer?offset=0&locale=sk&filterType=2&limit=1000'
    r = requests.get(url, headers={'authorization': access_token})
    r.raise_for_status()
    response = r.json()
    return [event['slug'] for event in response['events']]

def get_event_data(slug, access_token):
    url = f'https://www.cultusruzinov.sk/_api/wix-one-events-server/html/page-data/{slug}'
    print(url)
    r = requests.get(url, headers={'authorization': access_token})
    response = r.json()
    
    start_date = response['event']['scheduling']['config']['startDate']
    end_date = response['event']['scheduling']['config']['endDate']
    
    time_from = start_date.split('T')[1][:5]
    time_to = end_date.split('T')[1][:5]
    date = start_date.split('T')[0]
    
    return {
		'title': response['event']['title'],
		'description': f"{response['event']['description']}\n\n{response['event']['about']}".strip(),
		'url': f'https://www.cultusruzinov.sk/event-details/{slug}',
		'venue': response['event']['location']['name'],
		'city': response['event']['location']['fullAddress']['city'],
		'date': date,
		'time_from': time_from,
		'time_to': time_to,
	}

def main():
    print('Getting concerts for cultusruzinov.sk ...')
    access_token = get_access_token()
    
    n_attempts = 0
    max_attempts = 3
    while n_attempts < max_attempts:
        try:
            slugs = get_event_slugs(access_token)
            print(f'Found {len(slugs)} concerts. Fetching data ...')
            concert_data = [get_event_data(slug, access_token) for slug in slugs]
            break
        except Exception as e:
            print(f'Error: {e}')
            n_attempts += 1
            if n_attempts == max_attempts:
                raise e
        
    df = pd.DataFrame(concert_data, columns=['title', 'description', 'url', 'venue', 'city', 'date', 'time_from', 'time_to'])
    df.insert(0, 'source_url', 'https://www.cultusruzinov.sk')
    df.insert(0, 'source', 'Dom kultúry Ružinov')
    
    save_path = 'data/cultusruzinov_sk.csv'
    df.to_csv(save_path, index=False)
    print(f'Saved to {save_path}')
    
    # Convert DataFrame to list of dictionaries for API upload
    concert_data = df.to_dict(orient='records')
    print(f'Prepared {len(concert_data)} concerts for upload')

    print('Uploading concerts to the API ...')
    inserted_count, skipped_count = upload_potential_concerts(concert_data)
    print(f'Uploaded {inserted_count} concerts, skipped {skipped_count} concerts')
    
if __name__ == '__main__':
    main()