"""Normalize only metadata actually supplied by the upstream catalog."""

# Display metadata for the gateway's providers.
# - country: where the provider is headquartered (HQ, not necessarily where
#   every request is processed)
# - kind: "inference" = the provider runs its own or hosted models directly;
#   "gateway" = the provider routes requests across third-party model APIs.
PROVIDER_DETAILS = {
    "cortecs": {"country": "Austria (EU)", "kind": "gateway"},
    "mistral": {"country": "France (EU)", "kind": "inference"},
    "fikra": {"country": "Kenya", "kind": "inference"},
    "inceptron": {"country": "Sweden (EU)", "kind": "inference"},
    "aki": {"country": "Germany (EU)", "kind": "inference"},
    "ovh": {"country": "France (EU)", "kind": "inference"},
    "tensorx": {"country": "Ireland (EU)", "kind": "inference"},
}


def catalog_metadata(item):
    metadata = {}
    for field in ("max_context", "max_output_tokens"):
        value = item.get(field)
        if type(value) is int and 0 < value <= 2147483647:
            metadata[field] = value
    description = item.get("description")
    if isinstance(description, str) and description.strip():
        metadata["description"] = description[:10000]
    features = item.get("capabilities", item.get("supported_features"))
    if (
        isinstance(features, list)
        and len(features) <= 32
        and all(isinstance(v, str) and 0 < len(v) <= 64 for v in features)
    ):
        metadata["capabilities"] = features
    return metadata
