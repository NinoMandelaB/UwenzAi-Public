# capp/classes/ai/mistral_client.py

import logging
import requests
from typing import Dict, Any, Optional

from django.conf import settings
from capp.classes.ai import reasoning

logger = logging.getLogger(__name__)


class MistralClient:
    """
    Simple stateless client for the Mistral AI chat completions API.
    Mimics the way Stability AI is called in CharacterService.
    """

    @staticmethod
    def chat_completion(
        messages: list[Dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Send a chat completion request to Mistral.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            model: Override default model.
            reasoning_effort: the conversation's Fast/Deep toggle value
                ("fast"/"deep"). Mapped to Mistral's `reasoning_effort`
                request parameter via
                capp.classes.ai.reasoning.mistral_reasoning_kwargs -- a
                no-op unless the configured model is one of the exact
                models the current Mistral docs confirm support it for.
                See that module for cited docs.
            **kwargs: Additional parameters passed to the API.

        Returns:
            Parsed JSON response from Mistral.

        Raises:
            requests.exceptions.RequestException: On network or HTTP errors.
        """
        api_key = getattr(settings, "MISTRAL_API_KEY", None)
        api_url = getattr(settings, "MISTRAL_API_URL", None)

        if not api_key:
            logger.error("MISTRAL_API_KEY is not configured")
            raise ValueError("Mistral API key is not set")

        if not api_url:
            logger.error("MISTRAL_API_URL is not configured")
            raise ValueError("Mistral API URL is not set")

        model = model or getattr(settings, "MISTRAL_MODEL", "ministral-14b-2512")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # Include any extra API parameters passed as kwargs
        payload.update(kwargs)
        payload.update(reasoning.mistral_reasoning_kwargs(model, reasoning_effort or "fast"))

        try:
            logger.debug(
                "Mistral API request: model=%s, msgs=%d, max_tokens=%d",
                model,
                len(messages),
                max_tokens,
            )

            response = requests.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=60,  # Generous timeout for AI generation
            )

            # Raise for HTTP errors (4xx, 5xx)
            response.raise_for_status()

            data = response.json()
            logger.debug(
                "Mistral API response: id=%s, usage=%s",
                data.get("id"),
                data.get("usage"),
            )
            return data

        except requests.exceptions.Timeout:
            logger.error("Mistral API request timed out")
            raise
        except requests.exceptions.HTTPError as e:
            logger.error(
                "Mistral API HTTP error: %s - %s",
                e.response.status_code,
                e.response.text[:300],
            )
            raise
        except Exception:
            logger.exception("Unexpected error during Mistral API call")
            raise
