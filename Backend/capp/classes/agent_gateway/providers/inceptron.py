"""Inceptron provider for Agent Gateway (dedicated key)."""

from django.conf import settings
from capp.classes.agent_gateway.providers.openai_base import OpenAICompatibleProvider


class InceptronAgentProvider(OpenAICompatibleProvider):
    name = "inceptron"
    _currency = "USD"

    def __init__(self):
        self._api_key = getattr(settings, "INCEPTRON_UWENZAI_AGENT_API_KEY", None)
        self._base_url = getattr(settings, "INCEPTRON_UWENZAI_AGENT_BASE_URL", "https://api.inceptron.io/v1")
        self._models_cache_key = "agent_gateway_inceptron_models"
