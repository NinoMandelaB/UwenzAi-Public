# capp/classes/ai/tokenizer_factory.py
import logging
import threading
from collections import OrderedDict

from django.conf import settings
import tiktoken

logger = logging.getLogger(__name__)

# Map common Cortecs model IDs to tiktoken encodings
TIKTOKEN_MODEL_MAP = {
    "deepseek-v4-flash": "cl100k_base",
    "deepseek-v4": "cl100k_base",
    "gpt-4o-mini": "o200k_base",
    "gpt-4o": "cl100k_base",
    "claude-3-haiku-20240307": "cl100k_base",
    "bge-m3": "cl100k_base",
}

# mistral-common only registers tokenizer files for models with released
# weights, so public-preview and alias IDs (mistral-large-4,
# mistral-large-latest, mistral-small-2603, ...) are missing from its registry.
# The deprecated non-strict from_model() lookup resolves those IDs by substring
# to the wrong family — mistral-large-4 silently received the 2023 v3
# tokenizer — and emits a FutureWarning per call. Never use it: resolve
# unregistered IDs to a deterministic registered tokenizer for the same
# family instead:
#   - the large family moved to the v7 tokenizer with mistral-large-2411 and
#     continues on it (tekken v7) in later releases;
#   - other unregistered Mistral IDs reuse the current small-family tekken
#     tokenizer (mistral-small-2409) rather than the 2023 legacy vocab.
_MISTRAL_FALLBACK_CHAIN = (("mistral-large", ("mistral-large-2411",)),)
_MISTRAL_DEFAULT_FALLBACK = ("mistral-small-2409",)

# Resolved tokenizers are read-only after construction, so they are cached to
# avoid reloading the vocabulary on every message. Two properties matter:
#   - Keyed by the REGISTRY id that actually produced the tokenizer
#     (mistral-large-2411, mistral-small-2409, ...), never by the caller's
#     model string. Aliases (mistral-large-latest, mistral-large-4, ...) share
#     one instance, and arbitrary strings that merely contain "mistral" -
#     BYOK model names and the playground model field are validated only for
#     length before they are tokenized - cannot create entries.
#   - Bounded (LRU). Each loaded tokenizer holds roughly 60 MiB, so the cache
#     must not be able to grow with the number of distinct model ids in use.
_TOKENIZER_CACHE_MAX = 4
_tokenizer_cache: "OrderedDict[str, MistralTokenizerWrapper]" = OrderedDict()
_tokenizer_cache_lock = threading.Lock()


def get_tokenizer_for_model(model_name: str = None):
    """
    Return a tokenizer object with an `encode` method that returns a list of token IDs.
    For Mistral models, returns the official Mistral tokenizer (requires `mistral-common`).
    For other models, returns a tiktoken encoder.
    """
    if model_name is None:
        model_name = getattr(settings, "CORTECS_MODEL", None) or getattr(settings, "MISTRAL_MODEL", "mistral-small")

    model_lower = model_name.lower()

    # If it's a Mistral model, use the official tokenizer
    if "mistral" in model_lower or model_lower in ("open-mistral-nemo", "mistral-tiny"):
        try:
            from mistral_common.tokens.tokenizers.mistral import MistralTokenizer

            return _resolve_mistral_tokenizer(MistralTokenizer, model_name, model_lower)
        except Exception:
            logger.exception("Failed to load Mistral tokenizer, falling back to tiktoken")
            # Fallback to tiktoken

    # For all other models, use tiktoken
    encoding_name = TIKTOKEN_MODEL_MAP.get(model_name, "cl100k_base")
    try:
        enc = tiktoken.get_encoding(encoding_name)
    except Exception:
        enc = tiktoken.get_encoding("cl100k_base")
    return enc


def _cached_registry_tokenizer(MistralTokenizer, registry_id: str):
    """Return the cached wrapper for ``registry_id``, loading it on a miss.

    Raises when ``registry_id`` is not in the mistral-common registry (the
    strict lookup is an exact dictionary check, so a miss on an unknown name is
    cheap and loads nothing).
    """
    with _tokenizer_cache_lock:
        cached = _tokenizer_cache.get(registry_id)
        if cached is not None:
            _tokenizer_cache.move_to_end(registry_id)
            return cached

    # Load outside the lock: it takes about a second and must not stall cache hits.
    wrapper = MistralTokenizerWrapper(MistralTokenizer.from_model(registry_id, strict=True))

    with _tokenizer_cache_lock:
        existing = _tokenizer_cache.get(registry_id)
        if existing is not None:  # another thread loaded it first; keep one instance
            _tokenizer_cache.move_to_end(registry_id)
            return existing
        _tokenizer_cache[registry_id] = wrapper
        while len(_tokenizer_cache) > _TOKENIZER_CACHE_MAX:
            _tokenizer_cache.popitem(last=False)
    return wrapper


def _resolve_mistral_tokenizer(MistralTokenizer, model_name: str, model_lower: str):
    """Strict registry lookup with deterministic family fallbacks; never the deprecated non-strict path."""
    try:
        return _cached_registry_tokenizer(MistralTokenizer, model_name)
    except Exception:
        # Unregistered id (public preview or alias); resolve by family below.
        pass

    for prefix, candidates in _MISTRAL_FALLBACK_CHAIN:
        if prefix in model_lower:
            break
    else:
        candidates = _MISTRAL_DEFAULT_FALLBACK

    for candidate in candidates:
        try:
            wrapper = _cached_registry_tokenizer(MistralTokenizer, candidate)
        except Exception:
            continue
        logger.debug(
            "Mistral tokenizer: '%s' is not in the mistral-common registry; using the '%s' family tokenizer",
            model_name,
            candidate,
        )
        return wrapper

    raise RuntimeError(f"No registered Mistral fallback tokenizer for '{model_name}'")


class MistralTokenizerWrapper:
    """Wraps MistralTokenizer to provide a uniform `encode(text)` -> list of ints."""

    def __init__(self, mistral_tokenizer):
        self._tokenizer = mistral_tokenizer

    def encode(self, text: str) -> list:
        from mistral_common.protocol.instruct.messages import UserMessage
        from mistral_common.protocol.instruct.request import ChatCompletionRequest

        request = ChatCompletionRequest(messages=[UserMessage(content=text)])
        tokenized = self._tokenizer.encode_chat_completion(request)
        return tokenized.tokens
