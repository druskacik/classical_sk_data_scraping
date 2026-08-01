import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


_EMPTY_CITY_VALUES = {"", "-", "n/a", "na", "nan", "none", "null"}


@dataclass(frozen=True)
class CityResolution:
    city_id: int
    english_name: str
    local_name: str
    country_code: str


def clean_city_raw(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    city = re.sub(r"\s+", " ", str(value)).strip()
    return None if city.casefold() in _EMPTY_CITY_VALUES else city


def normalize_city_key(value: Any) -> str | None:
    city = clean_city_raw(value)
    if city is None:
        return None
    return unicodedata.normalize("NFKC", city).casefold()


def resolve_city(cursor, value: Any, source_scope: str | None = None) -> CityResolution | None:
    key = normalize_city_key(value)
    if key is None:
        return None
    if source_scope:
        cursor.execute(
            """
            SELECT DISTINCT c.id, c.english_name, c.local_name, c.country_code
            FROM city_alias a JOIN city c ON c.id = a.city_id
            WHERE a.normalized_alias = %s AND a.source_scope = %s
            """,
            (key, source_scope),
        )
        rows = cursor.fetchall()
        if len(rows) == 1:
            return CityResolution(*rows[0])
    cursor.execute(
        """
        SELECT DISTINCT c.id, c.english_name, c.local_name, c.country_code
        FROM city_alias a JOIN city c ON c.id = a.city_id
        WHERE a.normalized_alias = %s AND a.source_scope IS NULL
        """,
        (key,),
    )
    rows = cursor.fetchall()
    return CityResolution(*rows[0]) if len(rows) == 1 else None
