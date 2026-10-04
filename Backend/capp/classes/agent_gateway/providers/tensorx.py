"""TensorX provider for Agent Gateway (EU inference, dedicated key)."""

from django.conf import settings
from capp.classes.agent_gateway.providers.openai_base import OpenAICompatibleProvider


class TensorXAgentProvider(OpenAICompatibleProvider):
    name = "tensorx"
    _currency = "USD"

    def __init__(self):
        self._api_key = getattr(settings, "TENSORX_UWENZAI_AGENT_API_KEY", None)
        self._base_url = getattr(settings, "TENSORX_UWENZAI_AGENT_BASE_URL", "https://api.tensorx.ai/v1")
        self._models_cache_key = "agent_gateway_tensorx_models"
        # TensorX's /v1/models lists model IDs without prices; the operator-
        # maintained map in settings supplies USD prices per 1M tokens
        # (input, output).
        self._model_prices = {
            model_id: (prices[0], prices[1])
            for model_id, prices in (getattr(settings, "TENSORX_UWENZAI_AGENT_MODEL_PRICES", None) or {}).items()
        }
