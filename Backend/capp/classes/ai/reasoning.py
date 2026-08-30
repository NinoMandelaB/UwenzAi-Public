# capp/classes/ai/reasoning.py
"""
Per-provider mapping of the conversation-level Fast/Deep toggle
(``Conversation.reasoning_effort``) to the actual request parameters
understood by each AI provider wired into this app.

IMPORTANT: every mapping below was verified against the CURRENT official
provider documentation (fetched live on 2026-08-03) rather than assumed from
training knowledge, per product requirement. Doc URLs are cited next to each
mapping. See also /reasoning_effort_docs_verification.md in the repo/workspace
for the full research notes.

Cortecs (DeepSeek family models) and Mistral (specific models) use mappings
verified against each provider's official docs (see citations below).

BYOK (arbitrary user-supplied OpenAI-compatible endpoint/model) cannot be
verified against any specific provider's docs, since the endpoint is
third-party and user-chosen. Per product decision, BYOK instead gets a
BEST-EFFORT mapping: most OpenAI-compatible chat-completions servers accept
the same top-level `reasoning_effort` field OpenAI's own API uses, so we send
that and rely on the caller (capp.classes.byok.byok_service.BYOKService) to
catch a 400 "unknown/unrecognized parameter"-style rejection and retry once
without it -- so a BYOK endpoint that doesn't understand the parameter never
breaks the chat. This is explicitly unverified/speculative (unlike the
Cortecs/Mistral mappings) and flagged as such in the PR description.
"""

from typing import Dict, Optional

# Cortecs proxies to DeepSeek models. Reasoning/thinking control is only
# applied when the configured model is recognizably part of the DeepSeek
# family that the docs describe.
# Source: https://api-docs.deepseek.com/guides/thinking_mode/ (fetched 2026-08-03)
#   - `reasoning_effort` is a top-level chat completions request parameter.
#     Allowed values include "low", "high", "max" (default "high").
#   - `thinking` (enable/disable chain-of-thought) must be passed inside
#     `extra_body` as {"type": "enabled"} / {"type": "disabled"}.
#     Thinking is enabled by default upstream.
_DEEPSEEK_MODEL_PREFIXES = ("deepseek-",)


def cortecs_reasoning_kwargs(model: Optional[str], effort: str) -> Dict:
    """
    Build the extra kwargs to merge into a Cortecs (OpenAI-compatible)
    chat.completions.create(...) call for the given Fast/Deep toggle value.

    Returns {} (no-op / graceful degradation) unless the configured model is
    part of the DeepSeek family verified in the docs above.
    """
    if not model or not model.lower().startswith(_DEEPSEEK_MODEL_PREFIXES):
        return {}

    if effort == "deep":
        # Enable thinking mode and request high reasoning effort.
        return {
            "reasoning_effort": "high",
            "extra_body": {"thinking": {"type": "enabled"}},
        }

    # Fast (default): explicitly disable thinking mode so behavior matches
    # what the app did before this feature existed (DeepSeek enables
    # thinking by default upstream, so "fast" must actively opt out).
    return {"extra_body": {"thinking": {"type": "disabled"}}}


# Mistral direct client. Reasoning effort is only documented as supported
# for these exact models.
# Source: https://docs.mistral.ai/capabilities/reasoning/ (fetched 2026-08-03)
#   - `mistral-small-latest` and `mistral-medium-3-5` support the
#     `reasoning_effort` parameter ("high" enables full thinking trace,
#     "none" minimizes it).
# Source: https://docs.mistral.ai/api/ (fetched 2026-08-03)
#   - Confirms `reasoning_effort` is a top-level field on the
#     `/v1/chat/completions` request body (not nested under
#     `completion_args`), with allowed values none/minimal/low/medium/high/xhigh.
# Magistral models (`magistral-small-latest`, `magistral-medium-latest`) are
# deprecated per the same docs and use a different, unimplemented mechanism
# -- deliberately excluded here.
_MISTRAL_REASONING_MODELS = {"mistral-small-latest", "mistral-medium-3-5"}


def mistral_reasoning_kwargs(model: Optional[str], effort: str) -> Dict:
    """
    Build the extra kwargs to merge into a Mistral chat completion request
    for the given Fast/Deep toggle value.

    Returns {} (no-op / graceful degradation) unless the configured model is
    one of the exact models the docs confirm support `reasoning_effort` on.
    The app's actual default MISTRAL_MODEL (e.g. a ministral-* model) is NOT
    in this allow-list today, so this degrades gracefully out of the box;
    the mapping activates automatically if MISTRAL_MODEL is ever changed to
    one of the supported models.
    """
    if not model or model not in _MISTRAL_REASONING_MODELS:
        return {}

    if effort == "deep":
        return {"reasoning_effort": "high"}

    # Fast (default): omit the parameter entirely so current behavior is
    # byte-for-byte unchanged (we do not send "none").
    return {}


# BYOK: best-effort OpenAI-compatible mapping (NOT verified against any
# specific third-party provider's docs -- see module docstring). Sent
# optimistically; capp.classes.byok.byok_service.BYOKService.chat_completion
# retries once without this param if the endpoint rejects it with a 400
# "unknown/unrecognized parameter" style error, so this can never break a
# BYOK chat outright.
def byok_reasoning_kwargs(effort: str) -> Dict:
    """
    Build the best-effort extra kwargs for a BYOK (arbitrary OpenAI-compatible
    endpoint) chat completion request for the given Fast/Deep toggle value.

    "deep" -> {"reasoning_effort": "high"} (OpenAI's own field name/values).
    "fast" -> {} (omit the parameter entirely; matches pre-toggle behavior
    and avoids sending anything to endpoints that don't support reasoning at
    all).
    """
    if effort == "deep":
        return {"reasoning_effort": "high"}
    return {}


def reasoning_kwargs_for_provider(provider: str, model: Optional[str], effort: str) -> Dict:
    """
    Dispatch to the right per-provider mapping.

    provider: "cortecs" | "mistral" | "byok"
    effort: Conversation.ReasoningEffort value ("fast" | "deep")
    """
    effort = (effort or "fast").lower()
    if provider == "cortecs":
        return cortecs_reasoning_kwargs(model, effort)
    if provider == "mistral":
        return mistral_reasoning_kwargs(model, effort)
    if provider == "byok":
        # Best-effort only -- see byok_reasoning_kwargs() and module docstring.
        return byok_reasoning_kwargs(effort)
    # Any other unknown provider: no mapping -> graceful no-op.
    return {}
