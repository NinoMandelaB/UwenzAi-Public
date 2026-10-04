"""Text-only browser playground validation and bounded, opt-in encrypted history."""

import json
import uuid

from django.db import transaction
from django.utils import timezone

from capp.authentication import account_can_generate
from capp.classes.ai.token_utils import TokenUtils
from capp.models import AgentModelPrice, AgentPlaygroundHistory, CustomUser
from capp.utils.encryption import EncryptionService

PLAYGROUND_LIMITS = {"max_messages": 60, "max_input_tokens": 8192, "max_output_tokens": 1024}
MAX_MESSAGE_BYTES = 32768
MAX_SAVED_CONVERSATIONS = 20


def message_bytes(messages):
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def validate_pair(model, provider):
    if not isinstance(model, str) or not model or len(model) > 100:
        raise ValueError("Select a model from the catalog")
    if not isinstance(provider, str) or not provider or len(provider) > 50:
        raise ValueError("Select a provider from the catalog")
    return model, provider


def validate_playground(data):
    if not isinstance(data, dict):
        raise ValueError("Send a JSON object")
    allowed = {"key_id", "model", "provider", "messages", "max_tokens", "save_history", "client_operation_id"}
    if set(data) - allowed:
        raise ValueError(
            "Unsupported fields. Use an owned key_id and a catalog model/provider, not endpoint URLs or API keys"
        )
    model, provider = validate_pair(data.get("model"), data.get("provider"))
    try:
        key_id = uuid.UUID(str(data.get("key_id", "")))
        operation_id = str(uuid.UUID(str(data.get("client_operation_id", ""))))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("key_id and client_operation_id must be valid UUIDs") from exc
    save_history = data.get("save_history", False)
    if type(save_history) is not bool:
        raise ValueError("save_history must be true or false")
    max_tokens = data.get("max_tokens", 1024)
    if type(max_tokens) is not int or not 1 <= max_tokens <= PLAYGROUND_LIMITS["max_output_tokens"]:
        raise ValueError("max_tokens must be an integer between 1 and 1024")
    messages = data.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= PLAYGROUND_LIMITS["max_messages"]:
        raise ValueError("Send between 1 and 60 messages; start a new conversation if needed")
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message.get("role") not in ("user", "assistant")
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
        ):
            raise ValueError("Each message must contain only role (user or assistant) and non-empty text content")
    try:
        if message_bytes(messages) > MAX_MESSAGE_BYTES:
            raise ValueError("Messages exceed the 32768-byte playground limit; shorten the conversation")
    except UnicodeEncodeError as exc:
        raise ValueError("Messages must contain valid Unicode text") from exc
    if TokenUtils.count_message_tokens(messages, model=model) > PLAYGROUND_LIMITS["max_input_tokens"]:
        raise ValueError("Messages exceed 8192 input tokens; shorten the conversation or clear it")
    return {
        "key_id": key_id,
        "model": model,
        "provider": provider,
        "messages": messages,
        "max_tokens": max_tokens,
        "save_history": save_history,
        "client_operation_id": operation_id,
    }


def begin_history(user, model, provider):
    """Create a content-free save ticket. DELETE removes it; completion never recreates it."""
    EncryptionService.encrypt_value("[]")  # Fail before billing if encryption is not configured.
    with transaction.atomic():
        owner = CustomUser.objects.select_for_update().get(pk=user.pk)
        if not account_can_generate(owner):
            raise ValueError("Account is unavailable. Sign in with an active account")
        history = AgentPlaygroundHistory.objects.filter(user=user, model=model, provider=provider).first()
        if history:
            # Invalidate an older in-flight save for the same pair.
            history.revision = uuid.uuid4()
            history.save(update_fields=["revision", "updated_at"])
            return history.pk, history.revision
        if AgentPlaygroundHistory.objects.filter(user=user).count() >= MAX_SAVED_CONVERSATIONS:
            raise ValueError("You have 20 saved conversations. Delete one or disable Save history before continuing")
        history = AgentPlaygroundHistory.objects.create(user=user, model=model, provider=provider)
        return history.pk, history.revision


def save_history(ticket, user, model, provider, messages, response):
    choices = response.get("choices", [])
    if not choices:
        return False  # Idempotent replies intentionally contain no prompt/response content.
    text = choices[0].get("message", {}).get("content")
    if not isinstance(text, str):
        return False
    limit = PLAYGROUND_LIMITS["max_messages"]
    kept = (messages + [{"role": "assistant", "content": text}])[-limit:]
    price = AgentModelPrice.objects.filter(provider=provider, model=model).first()
    token_cap = PLAYGROUND_LIMITS["max_input_tokens"]
    if price and price.max_context:
        token_cap = max(0, min(token_cap, price.max_context - PLAYGROUND_LIMITS["max_output_tokens"]))
    while kept and (
        message_bytes(kept) > MAX_MESSAGE_BYTES or TokenUtils.count_message_tokens(kept, model=model) > token_cap
    ):
        kept.pop(0)
    payload = {"user": str(user.pk), "model": model, "provider": provider, "messages": kept}
    ciphertext = EncryptionService.encrypt_value(json.dumps(payload, ensure_ascii=False))
    return bool(
        AgentPlaygroundHistory.objects.filter(
            pk=ticket[0],
            revision=ticket[1],
            user=user,
            model=model,
            provider=provider,
        ).update(messages_encrypted=ciphertext, updated_at=timezone.now())
    )


def read_history(user, model, provider):
    history = AgentPlaygroundHistory.objects.filter(user=user, model=model, provider=provider).first()
    if not history or not history.messages_encrypted:
        return []
    data = json.loads(EncryptionService.decrypt_value(history.messages_encrypted))
    if data.get("user") != str(user.pk) or data.get("model") != model or data.get("provider") != provider:
        raise ValueError("Saved history could not be verified. Delete it and start a new conversation")
    return data["messages"]


def delete_history(user, model, provider):
    with transaction.atomic():
        # Same ordering as begin_history prevents delete versus first-save races.
        CustomUser.objects.select_for_update().get(pk=user.pk)
        AgentPlaygroundHistory.objects.filter(user=user, model=model, provider=provider).delete()
