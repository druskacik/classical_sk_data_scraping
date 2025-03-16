
import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv('API_URL')
API_KEY = os.getenv('API_KEY')

def upload_concerts(data: list[dict]):
    """
    Upload concerts to the API
    """
    response = requests.post(f'{API_URL}/api/add-concerts', json=data, headers={'API_KEY': API_KEY})
    print(response.json())
    return response.json()

class Concert:
    def __init__(self, title: str, date: str, source: str, time_from: str, time_to: str, city: str, venue: str, url: str, event_type: str):
        self.title = title
        self.date = date
        self.source = source
        self.time_from = time_from
        self.time_to = time_to
        self.city = city
        self.venue = venue
        self.url = url
        self.type = event_type

    def __str__(self):
        return f"{self.title} - {self.date} - {self.city} - {self.venue} - {self.url}"

    def __repr__(self):
        return self.__str__()
    
    def json(self):
        return {
            'title': self.title,
            'date': self.date,
            'time_from': self.time_from,
            'time_to': self.time_to,
            'city': self.city,
            'venue': self.venue,
            'url': self.url,
            'type': self.type,
        }

