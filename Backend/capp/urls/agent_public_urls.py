"""Inference-only routes mounted at /api/v1/ on the dedicated Agent service.

Both spellings dispatch directly so POST bodies never depend on slash redirects.
Management remains under the existing /api/v1/agent/ URL configuration.
"""

from django.urls import path

from capp.views.agent_gateway_views import agent_chat_completions, agent_models

urlpatterns = [
    path("models", agent_models, name="agent_public_models_sdk"),
    path("models/", agent_models, name="agent_public_models"),
    path("chat/completions", agent_chat_completions, name="agent_public_chat_completions_sdk"),
    path("chat/completions/", agent_chat_completions, name="agent_public_chat_completions"),
]
