"""AKI.IO provider for Agent Gateway (EU-hosted, dedicated key)."""

from django.conf import settings
from capp.classes.agent_gateway.providers.openai_base import OpenAICompatibleProvider


class AkiAgentProvider(OpenAICompatibleProvider):
    name = "aki"
    _currency = "EUR"

    def __init__(self):
        self._api_key = getattr(settings, "AKI_UWENZAI_AGENT_API_KEY", None)
        self._base_url = getattr(settings, "AKI_UWENZAI_AGENT_BASE_URL", "https://aki.io/openai/v1")
        self._models_cache_key = "agent_gateway_aki_models"
        # AKI's /openai/v1/models lists models without prices; the operator-
        # maintained map in settings supplies EUR prices per 1M tokens.
        self._model_prices = {
            model_id: (prices[0], prices[1])
            for model_id, prices in (getattr(settings, "AKI_UWENZAI_AGENT_MODEL_PRICES", None) or {}).items()
        }
