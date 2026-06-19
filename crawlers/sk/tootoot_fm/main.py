import requests

from ...base import BaseCrawler, CrawlerConfig

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
		'url': f"https://tootoot.fm/sk/events/{event['_id']}",
		'description': event['About']
    }

class TootootCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='tootoot_fm',
        source='tootoot',
        source_url='https://tootoot.fm',
        columns=['title', 'venue', 'city', 'time_from', 'time_to', 'date', 'url', 'description'],
        dedupe_subset=['title', 'date', 'time_from'],
        front_fields=[
            ('source_url', 'https://tootoot.fm'),
            ('source', 'tootoot'),
        ],
    )

    def scrape(self):
        url = 'https://api.tootoot.co/api/event/search?categories=548057368d4031089cea31f6&cityId=&page=0&perPage=99'
        r = requests.get(url)
        concerts = r.json()
        concerts = [c for c in concerts if validate_concert(c)]
        return [extract_concert_info(concert) for concert in concerts]


def main():
    TootootCrawler().run()


if __name__ == '__main__':
    main()
