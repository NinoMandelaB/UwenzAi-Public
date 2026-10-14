# play_billing_app/privacy.py
"""Financial evidence only: never retain raw customer data in Play payloads.

The Google Play ``purchases.products.get`` response carries no customer PII —
it describes the product, the purchase state and the order — but it is still a
provider payload rather than an authoritative column, so only an allowlist of
scalar reconciliation facts is ever stored. ``obfuscatedExternalAccountId`` and
similar identifier slots are deliberately excluded: UwenzAi never sets them, and
a value appearing there would be untrusted input, not evidence.
"""

_AUDIT_KEYS = (
    "kind",
    "productId",
    "purchaseState",
    "consumptionState",
    "purchaseTimeMillis",
    "developerPayload",
)


def play_purchase_audit_payload(data):
    """Copy scalar purchase facts, excluding nested/unexpected data."""
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key in _AUDIT_KEYS
        if (value := data.get(key)) is not None and isinstance(value, (str, int)) and not isinstance(value, bool)
    }
