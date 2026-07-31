# capp/classes/ai/token_utils.py
import logging
from typing import List, Dict, Optional
from capp.classes.ai.tokenizer_factory import get_tokenizer_for_model

logger = logging.getLogger(__name__)


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
    def count_message_tokens(cls, messages: List[Dict[str, str]], model: Optional[str] = None) -> int:
        """Count tokens for a list of chat messages."""
        if not messages:
            return 0
        tokenizer = get_tokenizer_for_model(model)
        # Build a single string representation to tokenize (simplified; for exact counts we'd need chat templates)
        # For consistent behaviour, we concatenate all message contents with spaces
        combined = " ".join(msg.get("content", "") for msg in messages)
        return len(tokenizer.encode(combined))

    @classmethod
    def truncate_messages(
        cls,
        messages: List[Dict[str, str]],
        max_tokens: int,
        min_messages: int = 2,
        model: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Truncate messages so total tokens <= max_tokens, preserving system message."""
        if not messages:
            return []

        system_msg = None
        other_msgs = list(messages)
        if other_msgs and other_msgs[0].get("role") == "system":
            system_msg = other_msgs.pop(0)

        if cls.count_message_tokens(messages, model) <= max_tokens:
            return messages

        kept = []
        for msg in reversed(other_msgs):
            tentative = []
            if system_msg:
                tentative.append(system_msg)
            tentative = tentative + [msg] + kept

            if len(kept) + 1 < min_messages:
                kept.insert(0, msg)
                continue

            if cls.count_message_tokens(tentative, model) <= max_tokens:
                kept.insert(0, msg)
            else:
                break

        result = []
        if system_msg:
            result.append(system_msg)
        result.extend(kept)
        return result
