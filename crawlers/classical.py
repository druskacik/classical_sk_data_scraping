import os
from dotenv import load_dotenv
import psycopg2
load_dotenv()

def upload_concerts(data: list[dict]):
    """
    Upload concerts to the database
    """
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASS = os.getenv('DB_PASS')
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT')
    
    conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASS, host=DB_HOST, port=DB_PORT)
    cursor = conn.cursor()
    
    new_concerts = []
    skipped_count = 0
    
    for concert in data:
        cursor.execute(
            "SELECT id FROM classical_concert WHERE title = %s AND date = %s AND url = %s",
            (concert['title'], concert['date'], concert['url'])
        )
        exists = cursor.fetchone()
        
        if not exists:
            new_concerts.append(concert)
        else:
            skipped_count += 1
    
    inserted_count = 0
    if new_concerts:
        for concert in new_concerts:
            cursor.execute(
                "INSERT INTO classical_concert (title, date, source, source_url, time_from, time_to, city, venue, url, type) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    concert['title'], 
                    concert['date'], 
                    concert['source'],
                    concert['source_url'],
                    concert.get('time_from'),
                    concert.get('time_to'), 
                    concert['city'], 
                    concert['venue'],
                    concert['url'], 
                    concert.get('type')
                )
            )
            inserted_count += 1
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return inserted_count, skipped_count

def upload_potential_concerts(data: list[dict]):
    """
    Upload potential concerts to the database
    """
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASS = os.getenv('DB_PASS')
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT')
    
    conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASS, host=DB_HOST, port=DB_PORT)
    cursor = conn.cursor()
    
    new_concerts = []
    skipped_count = 0
    
    for concert in data:
        cursor.execute(
            "SELECT id FROM potential_event WHERE title = %s AND date = %s AND url = %s",
            (concert['title'], concert['date'], concert['url'])
        )
        exists = cursor.fetchone()
        
        if not exists:
            new_concerts.append(concert)
        else:
            skipped_count += 1
    
    inserted_count = 0
    if new_concerts:
        for concert in new_concerts:
            cursor.execute(
                "INSERT INTO potential_event (title, date, source, source_url, time_from, time_to, city, venue, url, type) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    concert['title'], 
                    concert['date'], 
                    concert['source'],
                    concert['source_url'],
                    concert.get('time_from'),
                    concert.get('time_to'), 
                    concert['city'], 
                    concert['venue'],
                    concert['url'], 
                    concert.get('type')
                )
            )
            inserted_count += 1
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return inserted_count, skipped_count

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

