# capp/classes/search/query_builder.py
"""
Privacy-safe outbound search query construction.

Requirement: search queries must be derived from the topic, not sent
verbatim with user identifiers -- never include the user's id, display
name, or email in the outbound query string.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Identifier-shaped tokens. If any of these appear in a proposed query we
# FAIL CLOSED (return "", i.e. run no search at all) rather than stripping
# them and searching the remainder: a query like "who owns 0712345678" is
# meaningless without the number, and silently searching the rest still
# leaks intent to the provider. Refusing is both safer and more honest.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 8+ digits allowing spaces/dashes/parens/leading +: phone numbers, national
# IDs, account numbers. Deliberately not matching short numbers so ordinary
# topical queries ("top 10 songs 2026") are unaffected.
_PHONE_RE = re.compile(r"(?<!\w)\+?\d(?:[\d \-().]{6,})\d(?!\w)")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

_IDENTIFIER_PATTERNS = (
    ("email", _EMAIL_RE),
    ("phone-or-id", _PHONE_RE),
    ("uuid", _UUID_RE),
)

MAX_QUERY_LENGTH = 400


def build_search_query(topic: str, user=None) -> str:
    """
    Build a privacy-safe search query string from a topic.

    ``topic`` should already be the model's distilled search-tool-call
    argument (what it wants to look up), not the raw user message and not
    anything containing account identifiers.

    Returns ``""`` (meaning: do not search) when the topic contains an
    email address, a phone-number/national-ID-shaped digit run, a UUID, or
    -- when ``user`` is supplied -- any of that user's own identifying
    values. Callers treat an empty query as "skip this tool call".

    Kenya DPA note: outbound queries are sent to a third-party search
    provider (Staan) and appear in their logs, so identifiers must never
    reach them.
    """
    if not isinstance(topic, str) or not topic.strip():
        return ""

    for label, pattern in _IDENTIFIER_PATTERNS:
        if pattern.search(topic):
            logger.warning("Refusing web search: query contains %s-shaped identifier", label)
            return ""

    if user is not None:
        folded = topic.casefold()
        candidates = []
        for attr in ("pk", "user_id", "email", "username"):
            candidates.append(getattr(user, attr, None))
        get_full_name = getattr(user, "get_full_name", None)
        if callable(get_full_name):
            try:
                candidates.append(get_full_name())
            except Exception:  # pragma: no cover - defensive
                pass
        for value in candidates:
            text = str(value).strip() if value is not None else ""
            # Ignore very short values ('' or a 1-char username) which would
            # match almost any query and block search entirely.
            if len(text) >= 3 and text.casefold() in folded:
                logger.warning("Refusing web search: query contains the requesting user's own identifier")
                return ""

    query = _WHITESPACE_RE.sub(" ", topic).strip()
    return query[:MAX_QUERY_LENGTH]
