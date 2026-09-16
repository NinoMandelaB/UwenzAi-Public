"""OVHcloud AI Endpoints provider for Agent Gateway (dedicated key)."""

from django.conf import settings
from capp.classes.agent_gateway.providers.openai_base import OpenAICompatibleProvider


class OvhAgentProvider(OpenAICompatibleProvider):
    name = "ovh"
    _currency = "USD"

    def __init__(self):
        self._api_key = getattr(settings, "OVH_AI_UWENZAI_AGENT_API_KEY", None)
        self._base_url = getattr(
            settings, "OVH_AI_UWENZAI_AGENT_BASE_URL", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1"
        )
        self._models_cache_key = "agent_gateway_ovh_models"
