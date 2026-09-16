"""
Agent Gateway URL Configuration

Routes for the UwenzAi Agent Gateway API:
- /api/v1/agent/chat/completions - OpenAI-compatible chat completion (Bearer auth)
- /api/v1/agent/models - List available models (Bearer auth)
- /api/v1/agent/keys - Key management (Session auth)
- /api/v1/agent/usage - Usage reporting (Session auth)
"""

from django.urls import path

from capp.views.agent_gateway_views import (
    agent_models,
    agent_playground,
    agent_playground_history,
    agent_chat_completions,
    agent_gateway_consent,
    agent_gateway_pricing,
    AgentKeyViewSet,
    AgentUsageViewSet,
)

urlpatterns = [
    path("models", agent_models, name="agent_models_sdk"),
    path("chat/completions", agent_chat_completions, name="agent_chat_completions_sdk"),
    path("playground/", agent_playground, name="agent_playground"),
    path("playground/history/", agent_playground_history, name="agent_playground_history"),
    # Provider endpoints (Bearer token auth)
    # These are the main API endpoints that external agents call
    path("models/", agent_models, name="agent_models"),
    path("chat/completions/", agent_chat_completions, name="agent_chat_completions"),
    # Key management endpoints (Session auth)
    # These are for the UwenzAi-Dev page in the frontend
    path("keys/", AgentKeyViewSet.as_view({"get": "list", "post": "create"}), name="agent_keys"),
    path("keys/<uuid:pk>/", AgentKeyViewSet.as_view({"delete": "destroy"}), name="agent_key_delete"),
    path("usage/", AgentUsageViewSet.as_view({"get": "list"}), name="agent_usage"),
    # Consent endpoint (Session auth)
    path("consent/", agent_gateway_consent, name="agent_gateway_consent"),
    # Pricing catalog for the UwenzAi-Dev page (Session auth)
    path("pricing/", agent_gateway_pricing, name="agent_gateway_pricing"),
]
