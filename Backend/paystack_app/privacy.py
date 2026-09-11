"""Financial evidence only: never retain raw customer or authorization data."""


def payment_audit_payload(data):
    """Copy scalar reconciliation facts, excluding nested/unexpected data.

    Purchase intent, ownership and fulfillment are authoritative model columns.
    This is auxiliary provider evidence, not an identity or credential archive.
    """
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key in ("id", "reference", "amount", "currency", "status", "paid_at", "created_at", "fees", "channel")
        if (value := data.get(key)) is not None and isinstance(value, (str, int, float)) and not isinstance(value, bool)
    }
