# capp/classes/search/http_json.py
"""
Bounded JSON reading for third-party search-provider responses.

``response.json()`` buffers the entire body into memory and then parses it,
with no upper bound. A search provider is a third party: a bug, an incident,
or a compromise on their side can return a multi-hundred-megabyte body, and
several Celery workers doing that at once will OOM the container. The
provider being "trusted" is an availability assumption, not a memory-safety
one, so the body is capped the same way open_url caps fetched pages.
"""

import json
import logging

logger = logging.getLogger(__name__)

#: Generous relative to a normal 3-10 result search response (tens of KB),
#: small enough that a runaway body is refused long before it hurts.
DEFAULT_MAX_JSON_BYTES = 5_000_000


class ResponseTooLargeError(ValueError):
    """Raised when a provider response exceeds the byte cap."""


def read_json_bounded(response, max_bytes: int = DEFAULT_MAX_JSON_BYTES):
    """
    Read and parse a streaming ``requests`` response, aborting if it exceeds
    ``max_bytes``.

    The response MUST have been issued with ``stream=True``; otherwise the
    body is already fully buffered and the cap cannot help. Raises
    ``ResponseTooLargeError`` on overflow and ``ValueError`` on invalid JSON
    (matching ``response.json()``, so existing ``except ValueError`` handlers
    in the providers keep working -- ResponseTooLargeError subclasses it).
    """
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            response.close()
            raise ResponseTooLargeError(f"provider response exceeded {max_bytes} bytes")
        chunks.append(chunk)

    body = b"".join(chunks)
    encoding = response.encoding or "utf-8"
    try:
        text = body.decode(encoding, errors="replace")
    except LookupError:  # server declared a charset Python doesn't know
        text = body.decode("utf-8", errors="replace")
    return json.loads(text)
