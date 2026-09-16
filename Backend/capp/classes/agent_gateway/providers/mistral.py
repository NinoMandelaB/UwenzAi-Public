"""Mistral AI provider for Agent Gateway (direct, dedicated key)."""

from django.conf import settings
from capp.classes.agent_gateway.providers.openai_base import OpenAICompatibleProvider


class MistralAgentProvider(OpenAICompatibleProvider):
    name = "mistral"
    _currency = "USD"

    def __init__(self):
        self._api_key = getattr(settings, "MISTRAL_UWENZAI_AGENT_API_KEY", None)
        self._base_url = getattr(settings, "MISTRAL_UWENZAI_AGENT_BASE_URL", "https://api.mistral.ai/v1")
        self._models_cache_key = "agent_gateway_mistral_models"
        # Mistral's /v1/models lists models without prices; the operator-
        # maintained map in settings supplies USD prices per 1M tokens.
        self._model_prices = {
            model_id: (prices[0], prices[1])
            for model_id, prices in (getattr(settings, "MISTRAL_UWENZAI_AGENT_MODEL_PRICES", None) or {}).items()
        }
