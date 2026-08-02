import os
import time
import json
import logging
import psycopg2
from dotenv import load_dotenv

from google import genai
from google.genai import types

from analyzers.utils import generate_with_retry
from observability import configure_logging

load_dotenv()
logger = logging.getLogger(__name__)

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
    response = generate_with_retry(
        client,
        model="gemini-3.1-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="text/x.enum",
            response_schema={
                "type": "STRING",
                "enum": ["true", "false"],
            },
        ),
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
            id, title, date, url, source, source_url, time_from, time_to,
            city_raw, country_code_raw, city_id, country_code_resolved, venue, type, description
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
            INSERT INTO classical_concert
                (title, date, url, source, source_url, time_from, time_to,
                 city_raw, country_code_raw, city_id, country_code_resolved,
                 venue, type, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, concert[1:])
        else:
            skipped_count += 1
            
        cursor.execute("UPDATE potential_event SET added = true WHERE id = %s", (concert[0],))

    conn.commit()
    logger.info(
        "Potential-event upload completed",
        extra={
            "event": "potential_event_upload_completed",
            "inserted_count": len(concerts) - skipped_count,
            "skipped_count": skipped_count,
        },
    )

def main():
    configure_logging("classical-bot")
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
            logger.info(
                "Potential event analyzed",
                extra={
                    "event": "potential_event_analyzed",
                    "potential_event_id": event[0],
                    "is_classical_concert": output,
                },
            )
            update_potential_event(conn, event[0], output)
    else:
        logger.info(
            "No potential events to analyze",
            extra={"event": "potential_event_queue_empty"},
        )
        
    upload_classical_concerts(conn)
    conn.close()


if __name__ == '__main__':
    main()
