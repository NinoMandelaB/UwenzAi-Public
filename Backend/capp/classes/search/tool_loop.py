# capp/classes/search/tool_loop.py
"""
Server-side tool-calling loop for the per-chat web search toggle and the
open_url tool.

Design:
- Only OpenAI-compatible function/tool calling is used (Cortecs client uses
  the official `openai` SDK already; this reuses the same client instance;
  BYOK uses the same schema since BYOK endpoints are OpenAI-compatible too).
- web_search is capped at `settings.SEARCH_MAX_CALLS_PER_MESSAGE` (default 2)
  calls per user message to bound latency/cost.
- open_url is a separate tool, gated behind the exact same per-chat
  web_search_enabled toggle (offered only alongside web_search, never on its
  own), and capped independently at `settings.OPEN_URL_MAX_CALLS_PER_MESSAGE`
  (default 1) calls per user message.
- Results (search snippets or fetched-page text) are condensed and returned
  as a single string, meant to be appended at the END of the prompt (after
  all stable/cacheable sections) so that prompt-prefix caching for the rest
  of the system prompt is preserved. Callers (capp/tasks.py) are responsible
  for the actual placement.
"""

import json
import logging
import re
from typing import List, Optional, Tuple

from django.conf import settings

from capp.classes.search.base import get_search_provider, SearchResult
from capp.classes.search.query_builder import build_search_query
from capp.classes.search.sanitize import clip
from capp.classes.search.open_url import (
    OPEN_URL_TOOL_NAME,
    OpenURLError,
    open_url,
    format_open_url_result,
)

logger = logging.getLogger(__name__)

SEARCH_TOOL_NAME = "web_search"

# App-authored instructions, not web data. tasks injects these before the
# unchanged safety tail; unlike optional results they survive rebudgeting.
SEARCH_GROUNDING_NOTICE = (
    "For current information and follow-up requests for exact details, use the offered search tools now, "
    "not a promise to look something up later. Resolve relative dates such as 'this weekend' to calendar dates "
    "using the supplied current time and the user's relevant location. Include those dates and the event/location "
    "in a short topic-focused query, without private identifiers. Verify each result's event date and year, "
    "time and venue against the requested period; an annual event or a publication date is not proof it occurs then. "
    "Treat earlier assistant suggestions as unverified, not as evidence. Correct an earlier suggestion if the "
    "evidence does not support it. Do not claim you searched or verified anything without supporting tool results. "
    "Use only supported details, identify missing details honestly, and include a compact source URL where available."
)
TOOL_COMPLETION_NOTICE = (
    "This is the final answer for this turn; no more tools will run after it. Answer the user's request now "
    "using the tool outcomes actually provided, not a progress update or promise to fetch details later. "
    "Do not claim a lookup or action succeeded when it was refused, denied, failed, or returned no usable results. "
    "If results are absent or were omitted to fit the context, say you could not verify the requested details; "
    "do not fill gaps from earlier assistant claims. For events, give dates, times and locations only where the "
    "returned evidence supports the requested date range and year, with a compact source URL where available. "
    "Clearly state any remaining uncertainty in the user's language."
)
SEARCH_INCOMPLETE_REPLY = (
    "I couldn't verify the requested information in this reply, so I don't have confirmed details."
)
_LOOKUP_HANDOFF_RE = re.compile(
    r"(?:sorry[^.!?\n]{0,80}[.!?]\s*)?"
    r"(?:let me|i['’]ll|i will)\s+(?:just\s+|quickly\s+)?"
    r"(?:look up|check|fetch|grab|pull up)\s+(?:(?:the|those|these)\s+)?"
    r"(?:exact\s+|event\s+)?details(?:\s+for\s+(?:you|those events|these events))?"
    r"(?:\s+now)?[.!?]?",
    re.IGNORECASE,
)


def is_lookup_handoff(text: str) -> bool:
    """Catch short English lookup-only promises, not substantive answers.

    This is a narrow backstop for a known failure phrasing, not semantic
    factuality validation. Other languages/phrases rely on the grounding
    instructions; no extra generation or speculative search is performed.
    """
    return bool(_LOOKUP_HANDOFF_RE.fullmatch(text))


# Per-field caps for attacker-influenced search result data. Snippets are
# already clipped provider-side; titles and URLs were not bounded anywhere,
# and this block is inserted AFTER the prompt builder has done its context
# truncation -- so an unbounded field could push the follow-up request past
# the model's context limit and trigger full task retries.
MAX_QUERY_ECHO_CHARS = 400
MAX_TITLE_CHARS = 200
MAX_URL_CHARS = 2048
MAX_SNIPPET_CHARS = 500
MAX_SEARCH_BLOCK_CHARS = 4000

SEARCH_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": SEARCH_TOOL_NAME,
        "description": (
            "Search the web for current, factual information you don't "
            "already know (news, prices, recent events, real-world facts). "
            "Use when needed to answer accurately, including follow-up requests for exact details. "
            "For date-sensitive queries include the relevant calendar dates/year and location."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A short topic-focused search query (no personal names/emails/ids).",
                }
            },
            "required": ["query"],
        },
    },
}

OPEN_URL_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": OPEN_URL_TOOL_NAME,
        "description": (
            "Fetch a single web page and read its full content, when a snippet from "
            "web_search isn't enough detail. Only call this on a URL that either "
            "appeared in this conversation's web_search results, or that the user "
            "explicitly pasted into the chat themselves -- never on a URL you "
            "invented or guessed. You may call this at most once per message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "The exact http(s) URL to fetch, taken verbatim from search " "results or the user's message."
                    ),
                }
            },
            "required": ["url"],
        },
    },
}


def _condense_results(query: str, results: List[SearchResult]) -> str:
    """Render search results as a prompt block.

    Every interpolated value is attacker-influenced (anyone can publish a page
    and control its title/snippet, and the query echoes model input), so each
    field is delimiter-escaped and length-capped before composition. Without
    escaping, a crafted title can emit ``--- END WEB RESULTS ---`` and a forged
    ``=== ... (System) ===`` header to break out of the untrusted-data block.
    """
    safe_query = clip(query, MAX_QUERY_ECHO_CHARS)
    if not results:
        return f'No web search results found for "{safe_query}".'
    lines = [f'Web search results for "{safe_query}":']
    for i, r in enumerate(results, start=1):
        title = clip(getattr(r, "title", ""), MAX_TITLE_CHARS)
        url = clip(getattr(r, "url", ""), MAX_URL_CHARS)
        snippet = clip(getattr(r, "snippet", ""), MAX_SNIPPET_CHARS)
        lines.append(f"{i}. {title} ({url}) — {snippet}")
    return "\n".join(lines)[:MAX_SEARCH_BLOCK_CHARS]


def _get_function(call):
    return getattr(call, "function", None) or (call.get("function") if isinstance(call, dict) else None)


def _get_function_name(fn):
    return getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)


def _get_function_args(fn) -> dict:
    """Parse tool-call arguments, always returning a dict.

    A model is not a trusted schema enforcer: it can emit valid JSON that is
    not an object (``[]``), or fields of the wrong type. With BYOK the user
    also controls the endpoint entirely. Anything that is not a JSON object
    degrades to ``{}`` so a malformed call is skipped instead of raising out
    of the tool loop and retrying the whole AI task.
    """
    raw_args = getattr(fn, "arguments", None) or (fn.get("arguments") if isinstance(fn, dict) else "{}")
    try:
        parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _get_str_arg(args: dict, key: str) -> str:
    """Return ``args[key]`` only if it is genuinely a string, else "".

    Guards against schema-invalid types (``{"query": 7}``) that would
    otherwise raise deep inside the query builder or URL handling.
    """
    value = args.get(key)
    return value if isinstance(value, str) else ""


def run_search_tool_calls(
    tool_calls,
    max_calls: Optional[int] = None,
    *,
    user=None,
) -> Tuple[str, int]:
    """
    Execute up to `max_calls` search-tool invocations requested by the model.

    ``tool_calls`` is the OpenAI-compatible list of tool call objects/dicts
    (each with .function.name / .function.arguments, or dict-shaped
    equivalents). Only calls named SEARCH_TOOL_NAME are executed.

    Returns (condensed_block, calls_executed). ``condensed_block`` is a
    single string ready for the prompt builder to wrap as untrusted data and
    insert before the safety tail. Refused/malformed calls return an explicit
    outcome without counting as provider calls or echoing their arguments.
    Empty means no outcome was available (e.g. provider not configured);
    callers must still finalize, never reuse the tool-request preamble.
    """
    provider = get_search_provider()
    if provider is None:
        # Graceful degradation: no key configured, no crash, no outbound call.
        return "", 0

    cap = max_calls if max_calls is not None else getattr(settings, "SEARCH_MAX_CALLS_PER_MESSAGE", 2)

    blocks = []
    executed = 0
    for call in tool_calls or []:
        if executed >= cap:
            break

        fn = _get_function(call)
        if not fn:
            continue
        fn_name = _get_function_name(fn)
        if fn_name != SEARCH_TOOL_NAME:
            continue

        args = _get_function_args(fn)
        topic = _get_str_arg(args, "query")
        if not topic:
            blocks.append("[web_search failed: missing or invalid query; no search was performed]")
            continue
        query = build_search_query(topic, user=user)
        if not query:
            blocks.append("[web_search refused: query did not pass privacy validation; no search was performed]")
            continue

        try:
            results = provider.search(query, max_results=3)
        except Exception as e:  # defensive: never let a search failure crash the reply pipeline
            logger.warning("Search provider %s failed (%s)", provider.name, type(e).__name__)
            blocks.append("[web_search failed: provider unavailable; no results could be verified]")
            executed += 1
            continue

        blocks.append(_condense_results(query, results))
        executed += 1

    return ("\n\n".join(blocks), executed)


def run_open_url_tool_calls(
    tool_calls,
    max_calls: Optional[int] = None,
) -> Tuple[str, int]:
    """
    Execute up to `max_calls` open_url-tool invocations requested by the
    model. Only calls named OPEN_URL_TOOL_NAME are executed.

    Never raises: fetch/SSRF/extraction failures are caught and surfaced to
    the model as a short "couldn't fetch this URL" note, so a single bad URL
    can never crash the reply pipeline.

    Returns (condensed_block, calls_executed), same shape as
    run_search_tool_calls.
    """
    cap = max_calls if max_calls is not None else getattr(settings, "OPEN_URL_MAX_CALLS_PER_MESSAGE", 1)

    blocks = []
    executed = 0
    for call in tool_calls or []:
        if executed >= cap:
            break

        fn = _get_function(call)
        if not fn:
            continue
        fn_name = _get_function_name(fn)
        if fn_name != OPEN_URL_TOOL_NAME:
            continue

        args = _get_function_args(fn)
        url = _get_str_arg(args, "url").strip()
        if not url:
            continue

        try:
            result = open_url(url)
            blocks.append(format_open_url_result(result))
        except OpenURLError as e:
            logger.warning("open_url failed for url=%r: %s", url, e)
            # The URL and the error text both echo model/attacker input, so
            # they are escaped here too -- the failure path must not become
            # an easier injection route than the success path.
            safe_url = clip(url, MAX_URL_CHARS)
            safe_err = clip(str(e), MAX_SNIPPET_CHARS)
            blocks.append(f"=== OPEN_URL RESULT (System) ===\nURL: {safe_url}\nCould not fetch this page: {safe_err}")
        except Exception as e:  # defensive: never let a fetch failure crash the reply pipeline
            logger.exception("open_url unexpected failure for url=%r: %s", url, e)
            safe_url = clip(url, MAX_URL_CHARS)
            blocks.append(f"=== OPEN_URL RESULT (System) ===\nURL: {safe_url}\nCould not fetch this page.")

        executed += 1

    return ("\n\n".join(blocks), executed)


def run_tool_calls(
    tool_calls,
    search_max_calls: Optional[int] = None,
    open_url_max_calls: Optional[int] = None,
) -> Tuple[str, int, int]:
    """
    Convenience wrapper that runs both web_search and open_url tool calls
    present in a single model response.

    Returns (condensed_block, search_calls_executed, open_url_calls_executed).
    """
    search_block, search_used = run_search_tool_calls(tool_calls, max_calls=search_max_calls)
    open_url_block, open_url_used = run_open_url_tool_calls(tool_calls, max_calls=open_url_max_calls)

    blocks = [b for b in (search_block, open_url_block) if b]
    return ("\n\n".join(blocks), search_used, open_url_used)
