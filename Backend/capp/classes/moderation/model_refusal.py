# capp/classes/moderation/model_refusal.py
"""
Advisory model-refusal detection: the prompt-level backstop of the safety stack.

The deterministic classifier (``text_safety_policy.py``) screens user messages
BEFORE generation and decides enforcement. This module covers the second,
softer layer: the companion model itself refusing a request because the
permanent ``SAFETY CAUTION`` segment in the prompt (see
``capp/classes/ai/prompt_builder.py``) told it to stop.

Design (validated in the ``chatbot-testing`` experiments this feature is based
on — a short, always-present caution with a fixed refusal opening is the
prompt intervention that actually made small models refuse):

* The caution instructs the model to begin a refusal with the fixed marker
  ``[UWENZAI_SAFETY_REFUSAL]`` and then explain briefly in the user's own
  language. The marker is platform syntax exactly like ``[PHOTO_OFFER:...]``:
  the pipeline strips it before the reply is stored or shown, so the user
  only ever sees the human-language refusal.
* Two openers from the tested refusal templates are accepted as fallbacks
  for models that paraphrase instead of copying the marker. Fallback
  detection requires the reply to START with the opener (after stripping
  leading whitespace/markdown), which keeps false positives near zero — a
  message merely quoting the sentence mid-text does not count.
* A detected refusal is an ADVISORY signal only. The model is not a
  classifier: it can over- or under-refuse. Refusals are counted, shown to
  administrators in the admin panel and can trigger a system SUGGESTION to
  block the account — they never block, lock, score or aggravate anything
  on their own. The deterministic ledger remains the only enforcing layer.

This module stores no message text and no model output; it only extracts and
detects. The resulting ledger rows are written by
``SafetyLedgerService.record_model_refusal`` and carry metadata only.
"""

import re

# SafetyEvent category for a refusal the companion model produced itself.
CATEGORY_MODEL_REFUSAL = "model_refusal"

# Recorded on SafetyEvent.classifier_version so advisory rows are traceable
# to the exact detection rules that produced them.
MODEL_REFUSAL_DETECTOR_VERSION = "model-refusal/1"

# Fixed refusal marker the SAFETY CAUTION prompt segment instructs the model
# to begin refusals with. Stripped from the stored reply, never user-visible.
REFUSAL_MARKER = "[UWENZAI_SAFETY_REFUSAL]"

# Openers from the tested refusal templates (chatbot-testing and successors).
# Matched case-insensitively against the reply after leading whitespace and
# markdown decoration are stripped.
_FALLBACK_OPENERS = (
    "i have to stop the roleplay here",
    "i have to stop here",
)

_MARKER_RE = re.compile(re.escape(REFUSAL_MARKER), re.IGNORECASE)
# Leading decoration a model may put before the refusal sentence: whitespace,
# markdown emphasis, quote markers, dashes.
_LEADING_NOISE = " \t\r\n*`>#-\"'"


def extract_refusal(text):
    """Split a model reply into its user-visible text and a refusal flag.

    Returns ``(clean_text, refused)``. When the fixed marker is present it is
    removed (all occurrences, case-insensitive) and surrounding whitespace is
    collapsed; a marker-only reply comes back as an empty string with
    ``refused=True`` so callers can substitute a minimal visible line, exactly
    like the photo-offer marker handling. When no marker is present the text
    is returned unchanged and ``refused`` is True only if the reply opens with
    one of the fallback refusal sentences.
    """
    if not text:
        return "", False

    if _MARKER_RE.search(text):
        clean = _MARKER_RE.sub("", text)
        # Collapse the whitespace the removed marker left behind, but keep
        # the reply's own paragraph structure intact.
        clean = re.sub(r"[ \t]*\n[ \t]*\n[ \t]*", "\n", clean)
        clean = re.sub(r"[ \t]{2,}", " ", clean)
        return clean.strip(), True

    stripped = text.lstrip(_LEADING_NOISE).casefold()
    refused = any(stripped.startswith(opener) for opener in _FALLBACK_OPENERS)
    return text, refused


def is_model_refusal(text) -> bool:
    """Detection-only helper: does this model reply contain a refusal signal?"""
    return extract_refusal(text)[1]
