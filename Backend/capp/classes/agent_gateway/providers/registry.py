"""
Agent Gateway Provider Registry

Maintains a registry of available providers and resolves models to providers.
"""

from typing import Dict, Type

from capp.classes.agent_gateway.providers.base import AgentProvider, ModelInfo
from capp.classes.agent_gateway.providers.cortecs import CortecsAgentProvider
from capp.classes.agent_gateway.providers.mistral import MistralAgentProvider
from capp.classes.agent_gateway.providers.fikra import FikraAgentProvider
from capp.classes.agent_gateway.providers.inceptron import InceptronAgentProvider
from capp.classes.agent_gateway.providers.aki import AkiAgentProvider
from capp.classes.agent_gateway.providers.ovh import OvhAgentProvider
from capp.classes.agent_gateway.providers.tensorx import TensorXAgentProvider

# Provider registry: maps provider names to provider classes
PROVIDERS: Dict[str, Type[AgentProvider]] = {
    "cortecs": CortecsAgentProvider,
    "mistral": MistralAgentProvider,
    "fikra": FikraAgentProvider,
    "inceptron": InceptronAgentProvider,
    "aki": AkiAgentProvider,
    "ovh": OvhAgentProvider,
    "tensorx": TensorXAgentProvider,
}

# Provider instances cache
_provider_instances: Dict[str, AgentProvider] = {}


def get_provider(provider_name: str | None = None, model: str | None = None) -> AgentProvider | None:
    """
    Get a provider instance by name or resolve from model.

    If provider_name is given, returns that provider.
    If model is given, attempts to resolve which provider handles that model.
    A "provider/model" string (e.g. "cortecs/deepseek-v3.2") explicitly selects
    the provider; only a known provider name counts as a prefix, so model IDs
    that contain slashes themselves (Inceptron's vendor-prefixed IDs) keep
    working unchanged.

    Args:
        provider_name: Name of the provider (e.g., "cortecs")
        model: Model identifier (e.g., "deepseek-v4-flash")

    Returns:
        Configured provider instance or None
    """
    # "provider/model" prefix selects the provider explicitly.
    if model and not provider_name:
        prefix, sep, rest = model.partition("/")
        if sep and rest and prefix in PROVIDERS:
            provider_name = prefix
            model = rest

    # If provider_name is explicitly given, use it
    if provider_name:
        if provider_name not in PROVIDERS:
            return None
        if model:
            from capp.models import AgentModelPrice

            if not AgentModelPrice.objects.filter(provider=provider_name, model=model, is_active=True).exists():
                return None
        return _get_provider_instance(provider_name)

    # Otherwise, try to resolve from model
    if model:
        # P1.8: Check if the model is in the known allowlist before returning any provider.
        # First check the catalog (AgentModelPrice) to find which provider serves this model.
        from capp.classes.agent_gateway.pricing_service import cheapest_provider_for_model
        from capp.models import AgentModelPrice

        if AgentModelPrice.objects.filter(model=model).exists():
            # Deterministic default routing: the cheapest active catalog row
            # serves the request. An explicit catalog kill-switch (rows exist
            # but none active) must not fall back to provider defaults.
            chosen = cheapest_provider_for_model(model)
            return _get_provider_instance(chosen) if chosen else None

        # Check each provider's own model list
        for name in PROVIDERS:
            provider = _get_provider_instance(name)
            if provider is not None:
                try:
                    for m in provider.list_models():
                        if m.id == model:
                            return provider
                except Exception:
                    pass

        # Unknown model - do not default to any provider
        return None

    # Default to Cortecs if no provider or model specified
    return _get_provider_instance("cortecs")


def _get_provider_instance(provider_name: str) -> AgentProvider | None:
    """
    Get or create a provider instance.

    Args:
        provider_name: Name of the provider

    Returns:
        Provider instance or None if not configured or not in registry
    """
    if provider_name not in PROVIDERS:
        return None

    if provider_name not in _provider_instances:
        provider_class = PROVIDERS[provider_name]
        try:
            _provider_instances[provider_name] = provider_class()
        except Exception:
            # Provider may require configuration that's missing
            return None

    provider = _provider_instances[provider_name]

    # Check if provider is configured
    if not provider.is_configured():
        return None

    return provider


def get_available_providers() -> list[str]:
    """
    Get list of available (configured) provider names.

    Returns:
        List of provider names that are configured
    """
    available = []
    for name in PROVIDERS:
        provider = _get_provider_instance(name)
        if provider is not None:
            available.append(name)
    return available


def get_all_models() -> list[ModelInfo]:
    """
    Get all models from all configured providers.

    Returns:
        List of ModelInfo for all available models
    """
    all_models = []
    for name in PROVIDERS:
        provider = _get_provider_instance(name)
        if provider is not None:
            try:
                models = provider.list_models()
                all_models.extend(models)
            except Exception:
                # Skip providers that can't list models
                pass
    return all_models


def resolve_model_to_provider(model: str) -> tuple[str, AgentProvider] | None:
    """
    Resolve a model identifier to its provider.

    Args:
        model: Model identifier

    Returns:
        Tuple of (provider_name, provider_instance) or None if not found
    """
    for name in PROVIDERS:
        provider = _get_provider_instance(name)
        if provider is not None:
            try:
                models = provider.list_models()
                for m in models:
                    if m.id == model or m.name == model:
                        return (name, provider)
            except Exception:
                pass
    return None


def reset_registry():
    """
    Reset the provider instances cache. Useful for testing.
    """
    global _provider_instances
    _provider_instances = {}
