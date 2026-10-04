"""
Agent Gateway Provider Interface

This module defines the provider abstraction for the Agent Gateway.
All providers (Cortecs, Mistral, Fikira) must implement this interface.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentProvider(Protocol):
    """
    Protocol defining the interface for Agent Gateway providers.

    All providers must implement:
    - name: str - the provider identifier (e.g., "cortecs")
    - chat_completion: method to perform chat completion
    - list_models: method to list available models
    - is_configured: method to check if provider is configured
    """

    name: str

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
    ) -> "ProviderResult":
        """
        Execute a chat completion request.

        Args:
            model: The model identifier
            messages: List of message dicts (OpenAI format)
            params: Additional parameters (temperature, max_tokens, stream, etc.)

        Returns:
            ProviderResult with completion text, usage, and status
        """
        ...

    def list_models(self) -> list["ModelInfo"]:
        """
        List available models for this provider.

        Returns:
            List of ModelInfo dataclasses
        """
        ...

    def is_configured(self) -> bool:
        """
        Check if this provider is configured (has API key).

        Returns:
            True if configured, False otherwise
        """
        ...


@dataclass
class ModelInfo:
    """
    Information about a model available from a provider.

    Prices are stored in the provider's native currency (currency field).
    The pricing service converts to KES using the appropriate FX rate.
    """

    id: str
    name: str
    provider: str
    input_price_per_1m: Decimal
    output_price_per_1m: Decimal
    currency: str = "EUR"  # EUR, USD, etc.
    max_context: int | None = None
    description: str | None = None

    def __post_init__(self):
        if isinstance(self.input_price_per_1m, (int, float)):
            self.input_price_per_1m = Decimal(str(self.input_price_per_1m))
        if isinstance(self.output_price_per_1m, (int, float)):
            self.output_price_per_1m = Decimal(str(self.output_price_per_1m))


@dataclass
class ProviderResult:
    """
    Result from a provider's chat completion call.
    """

    text: str
    usage: dict[str, Any]  # Must contain prompt_tokens and completion_tokens
    status: str = "success"
    error_message: str | None = None
    # Structured tool calls from the assistant message (OpenAI shape:
    # [{"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}]).
    # None when the model answered in plain text. Relayed verbatim to the
    # client — the client, not the gateway, executes tools.
    tool_calls: list[dict[str, Any]] | None = None
    # Upstream HTTP status (int) when the failure was an HTTP response. The
    # raw error_message may echo request content and is never logged or
    # returned to callers; this numeric code is the safe diagnosable signal
    # (400 bad parameter vs 403 model access vs 404 unknown model).
    upstream_status_code: int | None = None
    finish_reason: str | None = None
    model: str | None = None

    @property
    def input_tokens(self) -> int:
        """Extract input tokens from usage block."""
        return self.usage.get("prompt_tokens", 0)

    @property
    def output_tokens(self) -> int:
        """Extract output tokens from usage block."""
        return self.usage.get("completion_tokens", 0)

    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return self.input_tokens + self.output_tokens

    def to_openai_response(self, model: str | None = None) -> dict[str, Any]:
        """
        Convert to OpenAI-compatible response format.

        Args:
            model: Model identifier to include in response

        Returns:
            OpenAI-compatible response dict
        """
        return {
            "id": f"uwenzai-{self.model or model or 'unknown'}",
            "object": "chat.completion",
            "created": 0,  # Will be set by view
            "model": model or self.model or "unknown",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": self.text,
                        **({"tool_calls": self.tool_calls} if self.tool_calls else {}),
                    },
                    "finish_reason": self.finish_reason or "stop",
                }
            ],
            "usage": self.usage,
        }
