import os
import time
import json
import psycopg2
from dotenv import load_dotenv
import pandas as pd
import jellyfish

from google import genai

load_dotenv()

PROMPT = """
You will receive composer name, and a JSON with ids and names of composers with similar names. Your role is to find the composer in the JSON and return the id.
If the composer is not found, return None. Output the ID of the composer only if you are absolutely sure that it's the correct composer.

Composer name: {composer_name}

JSON:
{json}
"""

def build_prompt(composer_name, json_data):
    return PROMPT.format(composer_name=composer_name, json=json_data)

def get_composer_id(client, composer_name, composers_dict):
    composers_json = json.dumps(composers_dict)
    possible_ids = composers_dict.keys()
    prompt = build_prompt(composer_name, composers_json)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config={
			'response_mime_type': 'text/x.enum',
			'response_schema': {
				"type": "STRING",
				"enum": list([str(i) for i in possible_ids]) + ["None"],
			},
		},
    )
    # Quota is 15 requests per minute
    time.sleep(4)
    
    return eval(response.text)

def get_unprocessed_concerts(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, composers FROM classical_concert
        WHERE CARDINALITY(composers) > 0 AND
        classical_concert.id NOT IN ( SELECT classical_concert_id FROM classical_concert_composer );
    """)
    return cursor.fetchall()

def find_composer(conn, composer_name):
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM composer WHERE name = %s", (composer_name,))
    result = cursor.fetchone()
    if result is None:
        return None
    return result[0]

def find_composers_with_similar_name(conn, composer_name):
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM composer")
    result = cursor.fetchall()
    
    composers_dict = {}
    for composer in result:
        if jellyfish.jaro_winkler_similarity(composer[1], composer_name) > 0.6:
            composers_dict[composer[0]] = composer[1]
    return composers_dict

def insert_new_composer(conn, composer_name, concert_ids, composer_id=None):
    cursor = conn.cursor()
    if composer_id is None:
        cursor.execute("INSERT INTO composer (name) VALUES (%s) RETURNING id", (composer_name,))
        composer_id = cursor.fetchone()[0]
    for concert_id in concert_ids:
        cursor.execute("INSERT INTO classical_concert_composer (classical_concert_id, composer_id) VALUES (%s, %s)", (concert_id, composer_id))
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
    concerts = get_unprocessed_concerts(conn)
    if len(concerts) > 0:
        df = pd.DataFrame(concerts, columns=["id", "composers"])
        df = df.explode("composers").rename(columns={"id": "classical_concert_id", "composers": "composer"})
        vc = df["composer"].value_counts()
        for composer in vc.index:
            concert_ids = df[df["composer"] == composer]["classical_concert_id"].tolist()
            composer_id = find_composer(conn, composer)
            if composer_id is not None:
                print(f"Composer {composer} found with id {composer_id}")
                insert_new_composer(conn, composer, concert_ids, composer_id)
            else:
                print(f"Composer {composer} not found")
                similar_composers = find_composers_with_similar_name(conn, composer)
                if len(similar_composers) == 0:
                    print(f"No similar composers found for {composer}")
                    insert_new_composer(conn, composer, concert_ids)
                else:
                    print(f"Similar composers found: {similar_composers}")
                    composer_id = get_composer_id(client, composer, similar_composers)
                    if composer_id is not None:
                        print(f"Composer {composer} found with id {composer_id}")
                        insert_new_composer(conn, composer, concert_ids, composer_id)
                    else:
                        print(f"Composer {composer} not found. Inserting as new composer.")
                        insert_new_composer(conn, composer, concert_ids)
    else:
        print("No potential events to analyze.")
        
    conn.close()

if __name__ == '__main__':
    main()
