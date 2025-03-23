import os
import json
import psycopg2
from dotenv import load_dotenv

from google import genai

load_dotenv()

PROMPT = """
You will receive a JSON with title and description of a classical music event. Your role is to extract the names of the composers from the description.

Output your response as a JSON list like this:
[
    "composer1",
    "composer2",
    "composer3"
]

Output full names of the composers, e.g. "Wolfgang Amadeus Mozart" or "Johann Sebastian Bach".

Here is the JSON:
{json}
"""

def build_prompt(json_data):
    return PROMPT.format(json=json_data)

def get_composers(client, json_data):
    prompt = build_prompt(json_data)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config={
			'response_mime_type': 'application/json',
			'response_schema': list[str],
		},
    )
    return response.parsed

def get_unprocessed_events(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, description FROM classical_concert WHERE is_concert_details_filled = false and description is not null")
    return cursor.fetchall()

def update_classical_concert(conn, idx, composers):
    cursor = conn.cursor()
    cursor.execute("UPDATE classical_concert SET is_concert_details_filled = true, composers = %s WHERE id = %s", (composers, idx))
    conn.commit()
    cursor.close()

def main():
    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    DB_NAME = os.getenv('DB_NAME')
    DB_USER = os.getenv('DB_USER')
    DB_PASS = os.getenv('DB_PASS')
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT')
    
    conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASS, host=DB_HOST, port=DB_PORT)
    concerts = get_unprocessed_events(conn)
    if len(concerts) > 0:
        for concert in concerts:
            json_data = json.dumps({'title': concert[1], 'description': concert[2]}, ensure_ascii=False)
            output = get_composers(client, json_data)
            print(f"Analyzed event {concert[1]}: {output}")
            update_classical_concert(conn, concert[0], output)
    else:
        print("No potential events to analyze.")
        
    conn.close()

if __name__ == '__main__':
    main()
