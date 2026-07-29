# capp/classes/ai/cortecs_client.py
import logging
import time
from typing import List, Dict, Optional

import openai
from openai import OpenAI
from django.conf import settings
from capp.classes.ai.mistral_client import MistralClient

logger = logging.getLogger(__name__)

# Transient errors worth retrying with backoff before giving up to Mistral.
_RETRYABLE_ERRORS = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
    openai.RateLimitError,
)
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 0.5


class CortecsClient:
    """
    OpenAI‑compatible client for Cortecs.ai.
    Tries Cortecs with the given model. On **any** failure (no key,
    no credits, timeout, server error, etc.) falls back instantly to
    the direct MistralClient, which is assumed to always be available.
    """

    @classmethod
    def chat_completion(
        cls,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> Dict:
        """
        Send a chat completion request via Cortecs.
        Falls back to MistralClient on failure.
        """
        api_key = getattr(settings, "CORTECS_API_KEY", None)
        base_url = getattr(settings, "CORTECS_BASE_URL", "https://api.cortecs.ai/v1")
        model = model or getattr(settings, "CORTECS_MODEL", None)

        # If no Cortecs key is configured, skip straight to Mistral
        if not api_key:
            logger.info("CORTECS_API_KEY not set – using Mistral directly")
            return MistralClient.chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        timeout = getattr(settings, "CORTECS_TIMEOUT", 60.0)

        # Try Cortecs with an explicit timeout and bounded retry/backoff for
        # transient errors before falling back to Mistral.
        try:
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
            response = cls._create_with_retry(
                client,
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            logger.info("Cortecs response received: id=%s model=%s", response.id, model)
            return {
                "id": response.id,
                "choices": [
                    {
                        "message": {
                            "content": response.choices[0].message.content,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
            }

        except Exception as e:
            # Any failure — no credits, timeout, bad gateway, invalid key, etc.
            logger.warning(
                "Cortecs failed (model=%s): %s – falling back to Mistral",
                model,
                e,
            )
            return MistralClient.chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

    @classmethod
    def _create_with_retry(cls, client, **kwargs):
        """Call the chat completion endpoint, retrying transient errors with
        exponential backoff. Non-transient errors propagate immediately."""
        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                return client.chat.completions.create(**kwargs)
            except _RETRYABLE_ERRORS as e:
                last_error = e
                if attempt == _MAX_RETRIES - 1:
                    break
                backoff = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "Cortecs transient error (attempt %d/%d): %s – retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    e,
                    backoff,
                )
                time.sleep(backoff)
        raise last_error
