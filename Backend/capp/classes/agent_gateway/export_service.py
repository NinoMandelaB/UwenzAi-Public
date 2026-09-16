"""Account-owned developer data only; no provider calls or cached exports."""

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from cryptography.fernet import Fernet
from django.conf import settings

from capp.models import AgentAPIKey, AgentGatewayOperation, AgentPlaygroundHistory, AgentUsageLog


class GatewayHistoryUnavailable(Exception):
    """The download must explicitly flag unreadable history, never silently omit it."""


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    return value


def _records(queryset, fields):
    # Explicit database and response allowlists: never serialize a whole model.
    return [
        {field: _json_value(value) for field, value in row.items()}
        for row in queryset.order_by("created_at", "pk").values(*fields)
    ]


def _history_messages(row, user):
    if not row["messages_encrypted"]:
        # begin_history persists a content-free ticket before inference. This
        # is not proof that a response or even an empty transcript was saved.
        return None
    try:
        key = settings.FIELD_ENCRYPTION_KEY
        key = key.strip().encode() if isinstance(key, str) else key
        # Do not use the logging decrypt helper here: exception messages (e.g.
        # decoding failures) can contain plaintext. Neither errors nor payloads
        # from this sensitive download belong in logs.
        plaintext = Fernet(key).decrypt(row["messages_encrypted"].encode()).decode("utf-8")
        payload = json.loads(plaintext)
        if (
            not isinstance(payload, dict)
            or payload.get("user") != str(user.pk)
            or payload.get("model") != row["model"]
            or payload.get("provider") != row["provider"]
        ):
            raise ValueError
        messages = payload["messages"]
        if not isinstance(messages, list):
            raise ValueError
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message["role"] not in ("user", "assistant")
                or not isinstance(message["content"], str)
            ):
                raise ValueError
        return [{"role": message["role"], "content": message["content"]} for message in messages]
    except Exception:
        # The caller preserves metadata with an explicit incomplete warning.
        # No ciphertext, decrypted content, or underlying exception escapes.
        raise GatewayHistoryUnavailable from None


def export_gateway_data(user):
    """Export retained records, not unsaved UI state or inferred execution outcomes.

    Decimal costs/FX are strings to preserve stored precision. Usage records
    include all dates/statuses and survive key deletion. Execution records are
    durable admission claims, not proof of completion. Client-supplied operation
    IDs are deliberately omitted: unlike record UUIDs they are arbitrary text.
    Persisted configuration is the key budget/expiry/label and history's
    provider/model pair; no current UI selection or max_tokens is stored.
    """
    keys = _records(
        AgentAPIKey.objects.filter(user=user),
        (
            "agent_api_key_id",
            "label",
            "key_hint",
            "is_active",
            "monthly_spark_budget",
            "expires_at",
            "last_used_at",
            "created_at",
            "updated_at",
        ),
    )
    owned_key_ids = {key["agent_api_key_id"] for key in keys}
    history = []
    warnings = []
    for row in (
        AgentPlaygroundHistory.objects.filter(user=user)
        .order_by("created_at", "pk")
        .values("id", "provider", "model", "created_at", "updated_at", "messages_encrypted")
    ):
        try:
            messages = _history_messages(row, user)
            content_status = "saved" if messages is not None else "not_saved"
        except GatewayHistoryUnavailable:
            messages = None
            content_status = "unavailable"
            warnings.append(
                {
                    "code": "gateway_history_unavailable",
                    "history_id": str(row["id"]),
                    "message": (
                        "Saved messages could not be decrypted or verified. "
                        "This export is incomplete; retry later or contact support."
                    ),
                }
            )
        history.append(
            {
                **{field: _json_value(row[field]) for field in ("id", "provider", "model", "created_at", "updated_at")},
                "content_status": content_status,
                "messages": messages,
            }
        )
    usage = _records(
        AgentUsageLog.objects.filter(user=user),
        (
            "usage_log_id",
            "key_id",
            "key_hint",
            "provider",
            "model",
            "input_tokens",
            "output_tokens",
            "cost_native",
            "cost_currency",
            "fx_rate",
            "sparks_charged",
            "status",
            "latency_ms",
            "created_at",
            "updated_at",
        ),
    )
    executions = _records(
        AgentGatewayOperation.objects.filter(user=user),
        ("id", "key_id", "provider", "model", "created_at", "updated_at"),
    )
    for record in usage + executions:
        # Defense in depth against inconsistent denormalized user/key ownership.
        # Keep the user's record, but never disclose another owner's key.
        if record["key_id"] is not None and record["key_id"] not in owned_key_ids:
            record["key_id"] = None
            if "key_hint" in record:
                record["key_hint"] = None
    return {
        "api_keys": keys,
        "playground_history": history,
        "usage_logs": usage,
        "execution_records": executions,
        "history_export_complete": not warnings,
        "warnings": warnings,
    }
