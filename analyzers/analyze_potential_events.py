import os
import time
import json
import psycopg2
from dotenv import load_dotenv

from google import genai

load_dotenv()

prompt = """
You will receive a JSON with various information about an event. Your role is to decide whether the event is classical music event or not.

Output "true" if the event is classical music event, otherwise output "false".

Here is the JSON:
{json}
"""

def build_prompt(json_data):
    return prompt.format(json=json_data)

def is_classical_music_event(client, json_data):
    prompt = build_prompt(json_data)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config={
			'response_mime_type': 'text/x.enum',
			'response_schema': {
				"type": "STRING",
				"enum": ["true", "false"],
			},
		},
    )
    # Quota is 15 requests per minute
    time.sleep(4)
    
    return response.text

def update_potential_event(conn, id, is_classical_concert):
    cursor = conn.cursor()
    cursor.execute("UPDATE potential_event SET analyzed = true, is_classical_concert = %s WHERE id = %s", (is_classical_concert, id))
    conn.commit()
    cursor.close()
    
def upload_classical_concerts(conn):
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT
            id, title, date, url, source, source_url, time_from, time_to, city, venue, type, description
        FROM potential_event
        WHERE is_classical_concert = true AND added = false;
    """)
    concerts = cursor.fetchall()
    
    skipped_count = 0
    for concert in concerts:
        title, date, url = concert[1], concert[2], concert[3]
        cursor.execute(
            "SELECT id FROM classical_concert WHERE title = %s AND date = %s AND url = %s",
            (title, date, url)
        )
        exists = cursor.fetchone()
        
        if not exists:
            cursor.execute("""
            INSERT INTO classical_concert (title, date, url, source, source_url, time_from, time_to, city, venue, type, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (concert[1], concert[2], concert[3], concert[4], concert[5], concert[6], concert[7], concert[8], concert[9], concert[10], concert[11]))
        else:
            skipped_count += 1
            
        cursor.execute("UPDATE potential_event SET added = true WHERE id = %s", (concert[0],))

    conn.commit()
    print(f"Skipped {skipped_count} concerts")
    print(f"Uploaded {len(concerts) - skipped_count} concerts")

def main():
    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASS = os.getenv('DB_PASS')
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT')
    
    conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASS, host=DB_HOST, port=DB_PORT)
    cursor = conn.cursor()  
    cursor.execute("SELECT id, title, url, venue, description FROM potential_event WHERE analyzed = false")
    potential_events = cursor.fetchall()
    column_names = [desc[0] for desc in cursor.description]
    if len(potential_events) > 0:
        for event in potential_events:
            event_json = json.dumps(dict(zip(column_names, event)), ensure_ascii=False)
            output = is_classical_music_event(client, event_json)
            output = True if output == 'true' else False
            print(f"Analyzed event {event_json}: {output}")
            update_potential_event(conn, event[0], output)
    else:
        print("No potential events to analyze.")
        
    upload_classical_concerts(conn)
    conn.close()


if __name__ == '__main__':
    main()
