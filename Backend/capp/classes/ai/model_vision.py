# capp/classes/ai/model_vision.py
"""
Which chat models can accept image input, and safe degradation helpers.

Photo messages must only carry ``image_url`` content parts to models that
actually understand images: sending an image part to a text-only model is a
hard provider error that would break the chat.

Resolution order for ``supports_vision(model_name)``:

  1. ``settings.VISION_MODEL_ALLOWLIST`` — env-driven CSV
     (``VISION_MODEL_ALLOWLIST=deepseek-flash,my-private-vlm``) of exact,
     case-insensitive matches. This is the authoritative operator control:
     a name listed here is vision-capable, full stop.
  2. A built-in exact-id set of known vision models whose names do not
     themselves prove vision (``deepseek-v4-flash``, the Cortecs series id,
     and its auto-update alias ``deepseek-v4-flash:latest``).
  3. A built-in pattern list of well-known vision-capable model ids,
     matched as substrings (``deepseek-flash``, ``gpt-4o``, ``pixtral``,
     …). Conservative by design: unknown text models are treated as
     text-only.

BYOK is deliberately NOT gated here — a user pointing UwenzAi at their own
endpoint is responsible for that model supporting image input (documented in
Settings). If their model rejects the image parts, the existing failure path
(refund + failed message) applies.
"""

import logging
from typing import Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# Substring patterns (lowercase) of widely-deployed multimodal chat models.
# Extend via settings.VISION_MODEL_ALLOWLIST rather than here whenever
# possible; this list only needs the well-known defaults.
_KNOWN_VISION_PATTERNS = (
    "deepseek-flash",  # DeepSeek-V4.1-Flash (native vision, 2026-09-10)
    "deepseek-v4.1",
    "deepseek-v4-flash-vision",  # retired alias that routes to V4.1-Flash
    "gemini",
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-5",
    "o4-mini",
    "claude-3",
    "claude-4",
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
    "pixtral",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "llama-3.2-vision",
    "llama-4",
    "vision",
)

# Exact ids (lowercase) whose NAME alone does not prove vision but which are
# known vision-capable today. "deepseek-v4-flash" is Cortecs' series id and
# "deepseek-v4-flash:latest" its auto-update alias, both currently routing to
# DeepSeek-V4.1-Flash (native vision). Matched EXACTLY, not as substrings:
# a dated snapshot like deepseek-v4-flash-0731 is the older text-only V4 Flash
# and must keep being refused. Caveat: if the auto-update alias ever moves to
# a text-only release, photo turns fail at the provider and are refunded —
# the operator escape hatch is VISION_MODEL_ALLOWLIST or pinning a snapshot.
_KNOWN_VISION_EXACT_IDS = frozenset(
    {
        "deepseek-v4-flash",
        "deepseek-v4-flash:latest",
    }
)


def _allowlist() -> List[str]:
    raw = getattr(settings, "VISION_MODEL_ALLOWLIST", "") or ""
    return [name.strip().lower() for name in raw.split(",") if name.strip()]


def supports_vision(model_name: Optional[str]) -> bool:
    """True when ``model_name`` accepts image content parts."""
    if not model_name:
        return False
    name = str(model_name).strip().lower()
    if not name:
        return False

    for allowed in _allowlist():
        if allowed == name:
            return True

    if name in _KNOWN_VISION_EXACT_IDS:
        return True

    return any(pattern in name for pattern in _KNOWN_VISION_PATTERNS)


def message_has_image_parts(messages: List[Dict]) -> bool:
    """True when any message carries multimodal content parts."""
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            return True
    return False


def strip_image_parts(
    messages: List[Dict],
    *,
    notice: str = "[The user attached a photo, but the active model could not view it.]",
) -> List[Dict]:
    """Convert multimodal messages back to plain text.

    Only used for safe degradation: when a provider switch lands the turn on
    a text-only model, images are removed and an explicit notice replaces
    them so the reply is honest about not having seen the photo. Never used
    silently on a paid image turn — the chat task fails those turns instead
    (refund path) so nobody pays for an unviewed photo.
    """
    stripped = []
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            stripped.append(message)
            continue
        texts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
        if not texts:
            texts = [notice]
        stripped.append({**message, "content": "\n".join(t for t in texts if t)})
    return stripped
