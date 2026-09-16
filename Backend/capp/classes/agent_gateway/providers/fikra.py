"""Fikra API provider for Agent Gateway (Kenyan AI platform, dedicated key)."""

from django.conf import settings
from capp.classes.agent_gateway.providers.openai_base import OpenAICompatibleProvider


class FikraAgentProvider(OpenAICompatibleProvider):
    name = "fikra"
    _currency = "USD"

    def __init__(self):
        self._api_key = getattr(settings, "FIKRA_UWENZAI_AGENT_API_KEY", None)
        self._base_url = getattr(settings, "FIKRA_UWENZAI_AGENT_BASE_URL", "https://api.fikraapi.co.ke/v1")
        self._models_cache_key = "agent_gateway_fikra_models"
        # Fikra's /v1/models lists models without prices; the operator-
        # maintained map in settings supplies USD prices per 1M tokens.
        self._model_prices = {
            model_id: (prices[0], prices[1])
            for model_id, prices in (getattr(settings, "FIKRA_UWENZAI_AGENT_MODEL_PRICES", None) or {}).items()
        }
