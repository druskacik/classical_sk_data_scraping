import requests
from datetime import datetime

from ...base import BaseCrawler, CrawlerConfig

def validate_schedule(schedule):
    """
    Validates if a schedule has a reasonable duration.
    
    Args:
        schedule (dict): A schedule dictionary from the API response
        
    Returns:
        bool: False if the event lasts more than 24 hours, True otherwise
    """
    start_at = schedule['attributes']['startAt']
    end_at = schedule['attributes']['endAt']
    
    # Convert to datetime objects
    start_datetime = datetime.fromisoformat(start_at.replace('Z', '+00:00'))
    end_datetime = datetime.fromisoformat(end_at.replace('Z', '+00:00'))
    
    # Calculate duration in hours
    duration_hours = (end_datetime - start_datetime).total_seconds() / 3600
    
    # Return False if duration is more than 24 hours
    return duration_hours <= 24

def extract_concert_data(response):
    
    schedules = response['schedules']
    cities = response['included']['cities']
    venues = response['included']['venues']
    events = response['included']['events']
    
    # Filter out schedules with unreasonable durations
    valid_schedules = [s for s in schedules if validate_schedule(s)]
    
    concert_data = []
    
    for schedule in valid_schedules:
        event_id = schedule['relationships']['event']['id']
        event = next((e for e in events if e['id'] == event_id), None)
        venue_id = schedule['relationships']['venue']['id']
        venue = next((v for v in venues if v['id'] == venue_id), None)
        city_id = venue['relationships']['city']['id']
        city = next((c for c in cities if c['id'] == city_id), None)
        
        concert_data.append({
            'title': event['locales']['sk']['name'],
            'venue': venue['locales']['sk']['name'],
            'city': city['locales']['sk']['name'],
            'date': schedule['attributes']['startAt'].split('T')[0].replace('-', '/'),
            'time_from': schedule['attributes']['startAt'].split('T')[1][:5],
            'time_to': schedule['attributes']['endAt'].split('T')[1][:5],
            'url': schedule['locales']['sk']['siteUrl'],
            'description': event['locales']['sk']['description'],
        })
    
    return concert_data

class GooutCrawler(BaseCrawler):
    config = CrawlerConfig(
        slug='goout_net',
        source='GoOut',
        source_url='https://goout.net',
        columns=['title', 'venue', 'city', 'date', 'time_from', 'time_to', 'url', 'description'],
        dedupe_subset=['title', 'date', 'time_from'],
        front_fields=[
            ('source_url', 'https://goout.net'),
            ('source', 'GoOut'),
        ],
    )

    def scrape(self):
        url = 'https://goout.net/services/entities/v1/schedules?languages%5B%5D=sk&categories%5B%5D=concerts&tags%5B%5D=classical&grouped=true&notScheduleTags%5B%5D=online&sort=popularity%3Adesc&limit=24&countryIsos%5B%5D=sk&include=events%2Cvenues%2Cimages%2Csales%2Ccities%2Cparents%2Cperformers'
        r = requests.get(url)
        response = r.json()
        return extract_concert_data(response)


def main():
    GooutCrawler().run()


if __name__ == '__main__':
    main()
