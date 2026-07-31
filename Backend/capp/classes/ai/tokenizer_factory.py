# capp/classes/ai/tokenizer_factory.py
import logging
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

            try:
                tokenizer = MistralTokenizer.from_model(model_name, strict=True)
            except Exception:
                logger.warning(
                    "Mistral tokenizer strict mode failed for '%s', trying non‑strict",
                    model_name,
                )
                tokenizer = MistralTokenizer.from_model(model_name, strict=False)
            # Return a wrapper that has a simple `encode` interface
            return MistralTokenizerWrapper(tokenizer)
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
