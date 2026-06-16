import random
import requests

from ..base import BaseCrawler, CrawlerConfig

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

class CultusRuzinovCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='cultusruzinov_sk',
        source='Dom kultúry Ružinov',
        source_url='https://www.cultusruzinov.sk',
        columns=['title', 'description', 'url', 'venue', 'city', 'date', 'time_from', 'time_to'],
        upload_target='potential',
        front_fields=[
            ('source_url', 'https://www.cultusruzinov.sk'),
            ('source', 'Dom kultúry Ružinov'),
        ],
    )

    def scrape(self):
        access_token = get_access_token()

        n_attempts = 0
        max_attempts = 3
        while n_attempts < max_attempts:
            try:
                slugs = get_event_slugs(access_token)
                print(f'Found {len(slugs)} concerts. Fetching data ...')
                return [get_event_data(slug, access_token) for slug in slugs]
            except Exception as e:
                print(f'Error: {e}')
                n_attempts += 1
                if n_attempts == max_attempts:
                    raise e

        return []


def main():
    CultusRuzinovCrawler().run()


if __name__ == '__main__':
    main()
