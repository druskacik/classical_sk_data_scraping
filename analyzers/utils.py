import time
import logging

from google.genai import types
from google.genai.errors import ServerError

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 5
_BASE_BACKOFF_SECONDS = 5


def generate_with_retry(
    client,
    *,
    model: str,
    contents: str,
    config: types.GenerateContentConfig,
    max_retries: int = _DEFAULT_MAX_RETRIES,
):
    """Call client.models.generate_content with exponential backoff on 5xx errors.

    Retries only on transient ServerError (HTTP 5xx). Any other exception
    (e.g. 4xx client errors, network issues) is re-raised immediately.
    """
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except ServerError as e:
            if e.code >= 500 and attempt < max_retries - 1:
                wait = _BASE_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning(
                    "API returned %s (attempt %d/%d). Retrying in %ds…",
                    e.code,
                    attempt + 1,
                    max_retries,
                    wait,
                )
                time.sleep(wait)
            else:
                raise
