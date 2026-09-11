# capp/classes/search/query_builder.py
"""
Privacy-aware outbound search query construction.

Policy (2026-09-10): the search provider is Staan, run by European
Perspective (Paris, France). Per Staan's privacy policy the API queries are
not linked to any person or device, processing is EU-based (OVH France) with
no US CLOUD Act exposure, and the account offers Zero Data Retention.
Because of that, public phone numbers and email addresses may appear in
outbound queries -- e.g. verifying the phone number of an embassy or a
business contact is a legitimate search.

Still refused (fail closed, return "" so no search runs at all):

* the requesting user's OWN identifiers (pk/user_id, email, username,
  display name, full name) -- data minimization under the Kenya DPA even
  with a no-logs provider;
* UUIDs -- internal object identifiers with no legitimate search use.

Refusals log the refused query verbatim: worker logs already contain full
prompts and user messages, so this adds no new exposure while making false
refusals diagnosable.
"""

import logging
import re

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

_IDENTIFIER_PATTERNS = (("uuid", _UUID_RE),)

MAX_QUERY_LENGTH = 400


def build_search_query(topic: str, user=None) -> str:
    """
    Build a privacy-aware search query string from a topic.

    ``topic`` should already be the model's distilled search-tool-call
    argument (what it wants to look up), not the raw user message.

    Returns ``""`` (meaning: do not search) when the topic contains a UUID
    or -- when ``user`` is supplied -- any of that user's own identifying
    values. Callers treat an empty query as "skip this tool call".
    """
    if not isinstance(topic, str) or not topic.strip():
        return ""

    # Normalize whitespace FIRST (NBSP/tab/newline become spaces) so the
    # own-identifier check below cannot be bypassed with exotic whitespace
    # that the normalizer would otherwise straighten out afterwards.
    query = _WHITESPACE_RE.sub(" ", topic).strip()
    if not query:
        return ""

    for label, pattern in _IDENTIFIER_PATTERNS:
        if pattern.search(query):
            logger.warning("Refusing web search: query contains %s-shaped identifier; refused query: %r", label, query)
            return ""

    if user is not None:
        folded = query.casefold()
        candidates = []
        for attr in ("pk", "user_id", "email", "username", "display_name"):
            candidates.append(getattr(user, attr, None))
        # CustomUser stores its email encrypted (CustomUser.email does not
        # exist); the plaintext address only surfaces via get_decrypted_email.
        # A model echoing the requesting user's own email must still refuse.
        get_decrypted_email = getattr(user, "get_decrypted_email", None)
        if callable(get_decrypted_email):
            try:
                candidates.append(get_decrypted_email())
            except Exception:  # pragma: no cover - defensive
                pass
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
                logger.warning(
                    "Refusing web search: query contains the requesting user's own identifier; refused query: %r",
                    query,
                )
                return ""

    return query[:MAX_QUERY_LENGTH]
