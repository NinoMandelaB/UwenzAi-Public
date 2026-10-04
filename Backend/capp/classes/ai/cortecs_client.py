# capp/classes/ai/cortecs_client.py
import logging
import time
from typing import List, Dict, Optional

import openai
from openai import OpenAI
from django.conf import settings
from capp.classes.ai.mistral_client import MistralClient
from capp.classes.ai import reasoning
from capp.classes.ai.model_vision import message_has_image_parts, supports_vision

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
        reasoning_effort: Optional[str] = None,
        **kwargs,
    ) -> Dict:
        """
        Send a chat completion request via Cortecs.
        Falls back to MistralClient on failure.

        ``reasoning_effort``: the conversation's Fast/Deep toggle value
        ("fast"/"deep"). Mapped to provider-specific kwargs via
        capp.classes.ai.reasoning.cortecs_reasoning_kwargs -- a no-op unless
        the configured model is a documented-supported DeepSeek model. See
        that module for cited docs.
        """
        api_key = getattr(settings, "CORTECS_API_KEY", None)
        base_url = getattr(settings, "CORTECS_BASE_URL", "https://api.cortecs.ai/v1")
        model = model or getattr(settings, "CORTECS_MODEL", None)

        # If no Cortecs key is configured, skip straight to Mistral.
        # `**kwargs` MUST be forwarded: it carries `tools` / `tool_choice`,
        # and dropping them silently disables web search and open_url on the
        # Mistral path (the model is simply never told the tools exist, so it
        # answers from memory and the user sees no error). `kwargs` is used
        # rather than `request_kwargs` because the latter adds Cortecs-
        # specific reasoning parameters Mistral does not accept.
        if not api_key:
            logger.info("CORTECS_API_KEY not set – using Mistral directly")
            return MistralClient.chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                **kwargs,
            )

        timeout = getattr(settings, "CORTECS_TIMEOUT", 60.0)

        reasoning_kwargs = reasoning.cortecs_reasoning_kwargs(model, reasoning_effort or "fast")
        request_kwargs = {**kwargs, **reasoning_kwargs}

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
                **request_kwargs,
            )
            logger.info("Cortecs response received: id=%s model=%s", response.id, model)
            message = response.choices[0].message
            message_dict = {"content": message.content}
            # Surface tool calls (e.g. the web_search tool) so tasks.py can
            # execute them and continue the loop. Absent unless the caller
            # passed `tools=[...]` and the model chose to call one.
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                message_dict["tool_calls"] = tool_calls
            return {
                "id": response.id,
                "choices": [
                    {
                        "message": message_dict,
                    }
                ],
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
            }

        except Exception as e:
            # A photo turn must never degrade to a text-only fallback: the
            # user asked the companion to LOOK at their photo, and a
            # text-only reply would be a paid non-answer. If the Mistral
            # fallback model cannot view images, fail the turn instead — the
            # task retries and ultimately refunds, so nobody pays for an
            # unviewed photo. (If Mistral IS vision-capable, the fallback
            # proceeds with the image parts intact.)
            if message_has_image_parts(messages):
                mistral_model = getattr(settings, "MISTRAL_MODEL", None)
                if not supports_vision(mistral_model):
                    logger.warning(
                        "Cortecs failed on a photo turn and the Mistral fallback (model=%s) cannot view images; "
                        "failing the turn so it is retried/refunded instead of answered text-only",
                        mistral_model,
                    )
                    raise

            # Any failure — no credits, timeout, bad gateway, invalid key, etc.
            logger.warning(
                "Cortecs failed (model=%s): %s – falling back to Mistral",
                model,
                e,
            )
            # Forward `**kwargs` (tools/tool_choice) so a Cortecs outage
            # degrades the provider, not the feature set. Without this the
            # fallback answer loses web search entirely and can confidently
            # state stale information instead of looking it up.
            return MistralClient.chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                **kwargs,
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
