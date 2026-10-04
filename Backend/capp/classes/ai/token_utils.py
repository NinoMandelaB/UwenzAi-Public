# capp/classes/ai/token_utils.py
import logging
import json
from typing import List, Dict, Optional
from capp.classes.ai.tokenizer_factory import get_tokenizer_for_model

logger = logging.getLogger(__name__)


class PromptBudgetExceeded(ValueError):
    """Mandatory instructions/current turn cannot safely fit the context."""


class TokenUtils:
    """Model‑aware token counting and truncation."""

    @classmethod
    def count_tokens(cls, text: str, model: Optional[str] = None) -> int:
        """Count tokens in a plain string using the tokenizer for the given model."""
        if not text:
            return 0
        tokenizer = get_tokenizer_for_model(model)
        return len(tokenizer.encode(text))

    @classmethod
    def count_message_tokens(
        cls, messages: List[Dict[str, str]], model: Optional[str] = None, tools: Optional[List[Dict]] = None
    ) -> int:
        """Count tokens for a list of chat messages.

        Multimodal content (a list of text/image parts) is counted by its
        text parts plus a fixed per-image estimate: serialising the base64
        data URLs would count megabytes of characters the model never stores
        as such, and evict the entire history from the budget.
        """
        if not messages and not tools:
            return 0
        tokenizer = get_tokenizer_for_model(model)
        # Count every message independently, including tool metadata. Reserve
        # framing per message and reply priming; concatenation undercounted
        # boundaries and entirely ignored tool-call arguments/results.
        total = 3
        for msg in messages:
            total += 8
            for value in msg.values():
                if isinstance(value, str):
                    total += len(tokenizer.encode(value))
                elif isinstance(value, list):
                    total += cls._count_content_parts(value, model)
                else:
                    text = json.dumps(value, ensure_ascii=False)
                    total += len(tokenizer.encode(text))
        if tools:
            total += 8 + len(tokenizer.encode(json.dumps(tools, ensure_ascii=False, sort_keys=True)))
        return total

    #: Fixed per-image budget cost (see settings PHOTO_IMAGE_TOKEN_ESTIMATE).
    _IMAGE_TOKEN_ESTIMATE = 512

    @classmethod
    def _count_content_parts(cls, parts: List, model: Optional[str] = None) -> int:
        """Token estimate for OpenAI-style content parts (text + image_url).

        Tool-call objects can also arrive as a list (the ``tool_calls`` key
        of an assistant message); those are serialised whole, exactly as the
        provider would receive them.
        """
        from django.conf import settings

        image_cost = max(0, int(getattr(settings, "PHOTO_IMAGE_TOKEN_ESTIMATE", 512)))
        tokenizer = get_tokenizer_for_model(model)
        total = 0
        for part in parts:
            if not isinstance(part, dict):
                text = json.dumps(part, ensure_ascii=False, default=str)
                total += len(tokenizer.encode(text))
                continue
            if part.get("type") == "image_url":
                total += image_cost
            elif "text" in part:
                total += len(tokenizer.encode(str(part.get("text") or "")))
            else:
                # Tool-call dicts and any other structured part.
                total += len(tokenizer.encode(json.dumps(part, ensure_ascii=False, default=str)))
        return total

    @classmethod
    def truncate_messages(
        cls,
        messages: List[Dict[str, str]],
        max_tokens: int,
        min_messages: int = 2,
        model: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Preserve the system and minimum newest messages, or fail closed."""
        if not messages:
            return []

        system_msg = None
        other_msgs = list(messages)
        if other_msgs and other_msgs[0].get("role") == "system":
            system_msg = other_msgs.pop(0)

        if cls.count_message_tokens(messages, model) <= max_tokens:
            return messages

        minimum = min(max(0, min_messages), len(other_msgs))
        prefix = [system_msg] if system_msg else []
        kept = other_msgs[-minimum:] if minimum else []
        if cls.count_message_tokens(prefix + kept, model) > max_tokens:
            raise PromptBudgetExceeded("prompt_budget_exceeded")
        remaining = other_msgs[:-minimum] if minimum else other_msgs
        for msg in reversed(remaining):
            if cls.count_message_tokens(prefix + [msg] + kept, model) > max_tokens:
                break
            kept.insert(0, msg)
        return prefix + kept
