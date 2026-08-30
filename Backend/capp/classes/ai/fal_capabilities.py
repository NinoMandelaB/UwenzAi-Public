# capp/classes/ai/fal_capabilities.py
"""
Per-endpoint capability map for fal.ai image-generation requests.

Why this exists
---------------
fal.ai validates every request body against a per-model Pydantic schema and
responds with a NON-RETRYABLE ``422 Unprocessable Content`` for any parameter
the model does not support (see
https://fal.ai/docs/documentation/model-apis/errors). Building payloads ad-hoc
against whatever endpoint ``FAL_TEXT_TO_IMAGE_MODEL`` / ``FAL_IMAGE_EDIT_MODEL``
happen to point at is therefore fragile: one parameter a model does not know
fails the whole request. All payload construction now goes through this module,
which knows — per endpoint — exactly which parameters exist and which values
they accept, and silently (with a debug log) drops anything else.

Data sources (verified against the official fal.ai API docs / per-endpoint
OpenAPI schemas, August 2026):

- Seedream V4 text-to-image:
  https://fal.ai/models/fal-ai/bytedance/seedream/v4/text-to-image/api
- Seedream V4 edit:
  https://fal.ai/models/fal-ai/bytedance/seedream/v4/edit/api
- Seedream V5 Lite text-to-image (current default t2i endpoint):
  https://fal.ai/models/fal-ai/bytedance/seedream/v5/lite/text-to-image/api
- Seedream V5 Lite edit (current default edit endpoint):
  https://fal.ai/models/fal-ai/bytedance/seedream/v5/lite/edit/api
- FLUX Pro v1.1:
  https://fal.ai/models/fal-ai/flux-pro/v1.1/api
- FLUX Pro v1.1 ultra (``safety_tolerance`` is API-only here):
  https://fal.ai/models/fal-ai/flux-pro/v1.1-ultra/api
- FLUX 2 Pro:
  https://fal.ai/models/fal-ai/flux-2-pro/api
- FLUX.1 dev:
  https://fal.ai/models/fal-ai/flux/dev/api
- FLUX.1 schnell:
  https://fal.ai/models/fal-ai/flux/schnell/api
- FLUX Pro Kontext (image-to-image; singular ``image_url``, ``aspect_ratio``
  INSTEAD of ``image_size``):
  https://fal.ai/models/fal-ai/flux-pro/kontext/api
- FLUX Pro Kontext multi (multi-image variant taking ``image_urls``):
  https://fal.ai/models/fal-ai/flux-pro/kontext/multi/llms.txt

Two provider behaviours every entry here had to respect:

- ``safety_tolerance`` exists ONLY on the FLUX Pro family (and is API-only on
  FLUX 1.1 ultra). Seedream has no such parameter, and FLUX.1 dev/schnell only
  have ``enable_safety_checker`` — never send ``safety_tolerance`` to them.
- Setting ``enable_safety_checker`` to ``False`` requires the fal account to be
  authorized for it; fal always runs its checker for unauthorized accounts
  regardless of the flag. We keep sending the product-required value — the
  application-level policy in ``capp.classes.moderation.image_prompt_policy``
  is the only real guarantee prohibited subjects are refused.

Parameter-name translation
--------------------------
Callers always speak the app's conventional names (``image_size`` presets and
``image_urls`` lists, as used for Seedream). When the selected endpoint's
schema names differ, ``build_fal_payload`` translates before validating:

- endpoints expecting ``image_url`` (SINGULAR string, e.g. FLUX Pro Kontext)
  receive the FIRST entry of a supplied ``image_urls`` list — a warning is
  logged when further URLs are dropped;
- endpoints expecting ``aspect_ratio`` (e.g. Kontext, which has no
  ``image_size``) receive the closest enum member for a supplied
  ``image_size`` — see ``_IMAGE_SIZE_TO_ASPECT_RATIO``.

Unknown / custom endpoints
--------------------------
Endpoint IDs are env-configurable. When a configured ID is not in
``FAL_ENDPOINT_CAPABILITIES`` we do NOT fail closed (that would take down image
generation the moment an operator points at a new model): the builder passes
through the conservative, universally-supported core set defined by
``_FALLBACK_CORE_PARAMS`` (prompt / image_size / num_images / max_images / seed
/ sync_mode / enable_safety_checker), drops everything else, and logs a
warning so the unknown endpoint is noticed and added to the map.
"""

import logging
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reusable constraint groups
# ---------------------------------------------------------------------------

#: image_size preset names shared by every fal endpoint used here.
_IMAGE_SIZE_PRESETS: FrozenSet[str] = frozenset(
    {
        "square_hd",
        "square",
        "portrait_4_3",
        "portrait_16_9",
        "landscape_4_3",
        "landscape_16_9",
        # Seedream-only auto-resolution presets (docs verified 2026-08).
        "auto_2K",
        "auto_3K",
        "auto_4K",
    }
)

#: safety_tolerance is a STRING enum on fal — "1".."6" on FLUX Pro v1/v1.1
#: (default "2"), "1".."5" on FLUX 2 Pro. Ints would fail schema validation.
_FLUX_PRO_V1_SAFETY_TOLERANCE: FrozenSet[str] = frozenset({"1", "2", "3", "4", "5", "6"})
_FLUX_2_PRO_SAFETY_TOLERANCE: FrozenSet[str] = frozenset({"1", "2", "3", "4", "5"})

#: enhance_prompt_mode enum on the Seedream v4/v5 endpoints.
_SEEDREAM_ENHANCE_PROMPT_MODES: FrozenSet[str] = frozenset({"standard", "fast"})

_OUTPUT_FORMATS: FrozenSet[str] = frozenset({"jpeg", "png"})

#: aspect_ratio enum on the FLUX Pro Kontext endpoints. Kontext takes an
#: aspect-ratio STRING where every other mapped endpoint takes ``image_size``
#: presets (or a ``{"width", "height"}`` dict). Verified against
#: https://fal.ai/models/fal-ai/flux-pro/kontext/api and
#: https://fal.ai/models/fal-ai/flux-pro/kontext/multi/llms.txt (2026-08).
#: A tuple (not a frozenset) so nearest-ratio tie-breaks are deterministic.
_KONTEXT_ASPECT_RATIOS: Tuple[str, ...] = (
    "21:9",
    "16:9",
    "4:3",
    "3:2",
    "1:1",
    "2:3",
    "3:4",
    "9:16",
    "9:21",
)

#: Translation of the app's ``image_size`` presets to the closest Kontext
#: ``aspect_ratio`` enum member. Note fal's naming: ``portrait_16_9`` is the
#: PORTRAIT orientation of 16:9, i.e. 9:16 (likewise ``portrait_4_3`` = 3:4).
#: The Seedream-only ``auto_2K``/``auto_3K``/``auto_4K`` presets carry no
#: ratio information and are deliberately NOT mapped: for those the param is
#: omitted (with a warning) so Kontext applies its own default geometry.
_IMAGE_SIZE_TO_ASPECT_RATIO: Mapping[str, str] = {
    "square_hd": "1:1",
    "square": "1:1",
    "portrait_4_3": "3:4",
    "portrait_16_9": "9:16",
    "landscape_4_3": "4:3",
    "landscape_16_9": "16:9",
}


def _params(**kwargs: Any) -> Dict[str, Dict[str, Any]]:
    """Small helper to keep the table below readable."""
    return kwargs


_SEEDREAM_COMMON_PARAMS: Dict[str, Dict[str, Any]] = _params(
    prompt={"required": True},
    image_size={"enum": _IMAGE_SIZE_PRESETS},  # ImageSize | Enum; dict form also allowed
    num_images={"min": 1, "max": 6, "default": 1},
    max_images={"min": 1, "max": 6, "default": 1},
    seed={},
    sync_mode={"default": False},
    # NOTE: setting this False requires fal account authorization —
    # unauthorized requests are always checked by fal regardless of the flag.
    enable_safety_checker={"default": True},
    enhance_prompt_mode={"enum": _SEEDREAM_ENHANCE_PROMPT_MODES, "default": "standard"},
)

#: Shared schema of the FLUX Pro Kontext family (image-to-image editing).
#: Verified against the official docs (2026-08):
#: https://fal.ai/models/fal-ai/flux-pro/kontext/api and
#: https://fal.ai/models/fal-ai/flux-pro/kontext/multi/llms.txt
#: Kontext differs from every other mapped endpoint in three ways:
#:   - ``aspect_ratio`` enum INSTEAD of ``image_size`` (NO image_size param);
#:   - ``safety_tolerance`` "1"-"6" (default "2") but NO enable_safety_checker;
#:   - a reference image is MANDATORY (image_url / image_urls per entry).
_KONTEXT_COMMON_PARAMS: Dict[str, Dict[str, Any]] = _params(
    prompt={"required": True},
    aspect_ratio={"enum": frozenset(_KONTEXT_ASPECT_RATIOS)},
    num_images={"min": 1, "max": 4, "default": 1},
    seed={},
    guidance_scale={"min": 1, "max": 20, "default": 3.5},
    sync_mode={"default": False},
    safety_tolerance={
        "enum": _FLUX_PRO_V1_SAFETY_TOLERANCE,
        "default": "2",
    },
    output_format={"enum": _OUTPUT_FORMATS, "default": "jpeg"},
)

# ---------------------------------------------------------------------------
# The capability map — single source of truth, keyed by canonical endpoint id.
# Normalisation in build_fal_payload() strips the "fal-ai/" namespace and
# leading/trailing slashes, so "fal-ai/flux/dev" and "flux/dev" both resolve.
# ---------------------------------------------------------------------------
FAL_ENDPOINT_CAPABILITIES: Mapping[str, Dict[str, Any]] = {
    # -- ByteDance Seedream V4 (t2i/edit) ------------------------------------
    # Docs: https://fal.ai/models/fal-ai/bytedance/seedream/v4/text-to-image/api
    # (prompt*, image_size, num_images/max_images 1-6, seed, sync_mode,
    #  enable_safety_checker, enhance_prompt_mode. NO safety_tolerance.)
    "fal-ai/bytedance/seedream/v4/text-to-image": {
        "docs_url": "https://fal.ai/models/fal-ai/bytedance/seedream/v4/text-to-image/api",
        "params": dict(_SEEDREAM_COMMON_PARAMS),
    },
    # Docs: https://fal.ai/models/fal-ai/bytedance/seedream/v4/edit/api
    # (t2i schema + image_urls*, up to 10 reference images)
    "fal-ai/bytedance/seedream/v4/edit": {
        "docs_url": "https://fal.ai/models/fal-ai/bytedance/seedream/v4/edit/api",
        "params": _params(
            **_SEEDREAM_COMMON_PARAMS,
            image_urls={"required": True, "max_items": 10},
        ),
    },
    # -- ByteDance Seedream V5 Lite (current production defaults) ------------
    # Docs: https://fal.ai/models/fal-ai/bytedance/seedream/v5/lite/text-to-image/api
    # Verified against the endpoint OpenAPI schema: prompt*, image_size,
    # num_images/max_images (1-6), seed, sync_mode, enable_safety_checker,
    # enhance_prompt_mode. NO safety_tolerance.
    "fal-ai/bytedance/seedream/v5/lite/text-to-image": {
        "docs_url": "https://fal.ai/models/fal-ai/bytedance/seedream/v5/lite/text-to-image/api",
        "params": dict(_SEEDREAM_COMMON_PARAMS),
    },
    # Docs: https://fal.ai/models/fal-ai/bytedance/seedream/v5/lite/edit/api
    # OpenAPI-verified: t2i schema + image_urls* (up to 10 images).
    "fal-ai/bytedance/seedream/v5/lite/edit": {
        "docs_url": "https://fal.ai/models/fal-ai/bytedance/seedream/v5/lite/edit/api",
        "params": _params(
            **_SEEDREAM_COMMON_PARAMS,
            image_urls={"required": True, "max_items": 10},
        ),
    },
    # -- FLUX -----------------------------------------------------------------
    # Docs: https://fal.ai/models/fal-ai/flux-pro/v1.1/api
    # safety_tolerance enum "1"-"6", default "2"; num_images 1-4;
    # output_format jpeg/png. (v1 endpoint shares the same schema.)
    "fal-ai/flux-pro/v1.1": {
        "docs_url": "https://fal.ai/models/fal-ai/flux-pro/v1.1/api",
        "params": _params(
            prompt={"required": True},
            image_size={"enum": _IMAGE_SIZE_PRESETS},
            num_images={"min": 1, "max": 4, "default": 1},
            seed={},
            sync_mode={"default": False},
            enable_safety_checker={"default": True},
            safety_tolerance={
                "enum": _FLUX_PRO_V1_SAFETY_TOLERANCE,
                "default": "2",
            },
            output_format={"enum": _OUTPUT_FORMATS, "default": "jpeg"},
        ),
    },
    # Docs: https://fal.ai/models/fal-ai/flux-pro/v1.1-ultra/api
    # Same constraints as v1.1. safety_tolerance is available ONLY via the API
    # (not the fal web playground) — sending it from the backend is correct.
    "fal-ai/flux-pro/v1.1-ultra": {
        "docs_url": "https://fal.ai/models/fal-ai/flux-pro/v1.1-ultra/api",
        "params": _params(
            prompt={"required": True},
            image_size={"enum": _IMAGE_SIZE_PRESETS},
            num_images={"min": 1, "max": 4, "default": 1},
            seed={},
            sync_mode={"default": False},
            enable_safety_checker={"default": True},
            safety_tolerance={
                "enum": _FLUX_PRO_V1_SAFETY_TOLERANCE,
                "default": "2",
            },
            output_format={"enum": _OUTPUT_FORMATS, "default": "jpeg"},
            raw={},
        ),
    },
    # Docs: https://fal.ai/models/fal-ai/flux-2-pro/api
    # safety_tolerance enum "1"-"5" (default "2") and API-only; NO num_images.
    "fal-ai/flux-2-pro": {
        "docs_url": "https://fal.ai/models/fal-ai/flux-2-pro/api",
        "params": _params(
            prompt={"required": True},
            image_size={"enum": _IMAGE_SIZE_PRESETS},
            seed={},
            sync_mode={"default": False},
            enable_safety_checker={"default": True},
            safety_tolerance={
                "enum": _FLUX_2_PRO_SAFETY_TOLERANCE,
                "default": "2",
            },
            output_format={"enum": _OUTPUT_FORMATS, "default": "jpeg"},
        ),
    },
    # Docs: https://fal.ai/models/fal-ai/flux/dev/api
    # enable_safety_checker yes, but NO safety_tolerance (dev/schnell differ
    # from the Pro family here).
    "fal-ai/flux/dev": {
        "docs_url": "https://fal.ai/models/fal-ai/flux/dev/api",
        "params": _params(
            prompt={"required": True},
            image_size={"enum": _IMAGE_SIZE_PRESETS},
            num_images={"min": 1, "max": 4, "default": 1},
            seed={},
            sync_mode={"default": False},
            enable_safety_checker={"default": True},
        ),
    },
    # Docs: https://fal.ai/models/fal-ai/flux/schnell/api
    "fal-ai/flux/schnell": {
        "docs_url": "https://fal.ai/models/fal-ai/flux/schnell/api",
        "params": _params(
            prompt={"required": True},
            image_size={"enum": _IMAGE_SIZE_PRESETS},
            num_images={"min": 1, "max": 4, "default": 1},
            seed={},
            sync_mode={"default": False},
            enable_safety_checker={"default": True},
        ),
    },
    # -- FLUX Pro Kontext (image-to-image editing) ---------------------------
    # Docs: https://fal.ai/models/fal-ai/flux-pro/kontext/api
    # Verified: prompt*, image_url* (SINGULAR string), aspect_ratio enum (NO
    # image_size), num_images 1-4, seed, guidance_scale 1-20 (default 3.5),
    # sync_mode, output_format jpeg/png, safety_tolerance "1"-"6" (default
    # "2"), enhance_prompt. NO image_urls, NO enable_safety_checker.
    # Switch to it with FAL_IMAGE_EDIT_MODEL=fal-ai/flux-pro/kontext — the
    # builder translates image_urls->image_url and image_size->aspect_ratio.
    "fal-ai/flux-pro/kontext": {
        "docs_url": "https://fal.ai/models/fal-ai/flux-pro/kontext/api",
        "params": _params(
            **_KONTEXT_COMMON_PARAMS,
            image_url={"required": True},
            enhance_prompt={"default": False},
        ),
    },
    # Docs: https://fal.ai/models/fal-ai/flux-pro/kontext/multi/llms.txt
    # Multi-image variant: image_urls* (LIST of strings, no documented max)
    # instead of the singular image_url. NOTE: multi's schema does NOT list
    # enhance_prompt, so it is not accepted here — only on single kontext.
    "fal-ai/flux-pro/kontext/multi": {
        "docs_url": "https://fal.ai/models/fal-ai/flux-pro/kontext/multi/llms.txt",
        "params": _params(
            **_KONTEXT_COMMON_PARAMS,
            image_urls={"required": True},
        ),
    },
}

#: Conservative pass-through set for endpoint IDs not present in the map
#: (env-configured custom models). These params are supported by every fal
#: image endpoint we have ever shipped against; anything else is dropped.
_FALLBACK_CORE_PARAMS: FrozenSet[str] = frozenset(
    {
        "prompt",
        "image_size",
        "num_images",
        "max_images",
        "seed",
        "sync_mode",
        "enable_safety_checker",
    }
)


def normalize_endpoint_id(endpoint_id: str) -> str:
    """Canonicalise a configured model id for capability lookups.

    Settings store ids without the ``fal-ai/`` namespace (e.g.
    ``bytedance/seedream/v5/lite/text-to-image``) while the map and the fal
    docs use the canonical ``fal-ai/...`` form, so both spellings resolve to
    the same entry.
    """
    cleaned = (endpoint_id or "").strip().strip("/")
    if cleaned and not cleaned.startswith("fal-ai/"):
        cleaned = f"fal-ai/{cleaned}"
    return cleaned


def get_endpoint_capabilities(endpoint_id: str) -> Optional[Dict[str, Any]]:
    """Return the capability entry for ``endpoint_id``, or None if unknown."""
    return FAL_ENDPOINT_CAPABILITIES.get(normalize_endpoint_id(endpoint_id))


def build_fal_payload(
    endpoint_id: str,
    desired_params: Mapping[str, Any],
) -> Tuple[Dict[str, Any], FrozenSet]:
    """Build a request payload for ``endpoint_id`` from ``desired_params``.

    Only parameters the endpoint actually supports are emitted:

    - caller params are first TRANSLATED to the endpoint's schema names where
      those differ from the app's conventional ones (``image_urls`` ->
      ``image_url``, ``image_size`` -> ``aspect_ratio`` — see
      ``_translate_param_names``);
    - unknown params are dropped with a ``logger.debug`` line
      ("param X not supported by endpoint Y, dropping") — never sent;
    - enum values outside the endpoint's enum fall back to the endpoint
      default when one is known (dropped otherwise), with a warning;
    - numeric values outside a documented min/max range are clamped, with a
      warning;
    - empty/None values are dropped (absent = provider default on fal);
    - list values honour a documented ``max_items`` by truncation.

    Returns ``(payload, dropped_param_names)``. For unknown endpoints the
    conservative core set is passed through (see module docstring) and a
    warning is logged.
    """
    endpoint_key = normalize_endpoint_id(endpoint_id)
    entry = FAL_ENDPOINT_CAPABILITIES.get(endpoint_key)
    if entry is None:
        logger.warning(
            "fal endpoint %s has no capability entry; passing through only the "
            "conservative core params %s — add a FAL_ENDPOINT_CAPABILITIES entry",
            endpoint_key,
            sorted(_FALLBACK_CORE_PARAMS),
        )
        param_specs = {name: {} for name in _FALLBACK_CORE_PARAMS}
    else:
        param_specs = entry["params"]

    desired_params, pre_dropped = _translate_param_names(param_specs, desired_params, endpoint_key)

    payload: Dict[str, Any] = {}
    dropped = set(pre_dropped)

    for name, value in desired_params.items():
        spec = param_specs.get(name)
        if spec is None:
            dropped.add(name)
            logger.debug("param %s not supported by endpoint %s, dropping", name, endpoint_key)
            continue

        # None / empty-string mean "not set": omit so fal applies its default.
        if value is None or value == "":
            dropped.add(name)
            logger.debug("param %s for endpoint %s is empty, omitting", name, endpoint_key)
            continue

        value = _apply_constraints(name, value, spec, endpoint_key)
        if value is _DROP:
            dropped.add(name)
            continue

        payload[name] = value

    return payload, frozenset(dropped)


def _translate_param_names(
    param_specs: Mapping[str, Dict[str, Any]],
    desired_params: Mapping[str, Any],
    endpoint_key: str,
) -> Tuple[Dict[str, Any], FrozenSet]:
    """Rename caller params to the endpoint's schema names where they differ.

    Callers always use the app's conventional names (``image_size`` presets /
    ``image_urls`` lists, as for Seedream). Two translations exist:

    - ``image_urls`` (list) -> ``image_url`` (singular) when the endpoint
      accepts only one reference image (FLUX Pro Kontext): the FIRST URL is
      used and any further URLs are dropped with a warning (the repo's
      convention for silent data loss, mirroring clamp/truncate warnings);
    - ``image_size`` -> ``aspect_ratio`` when the endpoint takes aspect
      ratios (Kontext): presets map via ``_IMAGE_SIZE_TO_ASPECT_RATIO`` and
      ``{"width", "height"}`` dicts map to the numerically closest enum
      member; unmappable values (e.g. Seedream's ``auto_*`` presets) are
      dropped with a warning so the provider default applies.

    An explicitly supplied target name always wins over a translated one (a
    redundant source param is then dropped by the normal unsupported-param
    path). Returns ``(translated_params, names_dropped_by_translation)``.
    """
    translated = dict(desired_params)
    dropped = set()

    single_image = "image_url" in param_specs and "image_urls" not in param_specs
    if single_image and "image_urls" in translated and "image_url" not in translated:
        urls = translated.pop("image_urls")
        if isinstance(urls, (list, tuple)) and urls:
            if len(urls) > 1:
                logger.warning(
                    "endpoint %s accepts a single image_url; using the first of %d image_urls, dropping the rest",
                    endpoint_key,
                    len(urls),
                )
            translated["image_url"] = urls[0]
        else:
            dropped.add("image_urls")
            logger.warning(
                "param image_urls for endpoint %s is not a non-empty list; cannot translate to image_url, dropping",
                endpoint_key,
            )

    ratio_only = "aspect_ratio" in param_specs and "image_size" not in param_specs
    if ratio_only and "image_size" in translated and "aspect_ratio" not in translated:
        image_size = translated.pop("image_size")
        aspect_ratio = _image_size_to_aspect_ratio(image_size, endpoint_key)
        if aspect_ratio is None:
            dropped.add("image_size")
        else:
            translated["aspect_ratio"] = aspect_ratio

    return translated, frozenset(dropped)


def _image_size_to_aspect_ratio(image_size: Any, endpoint_key: str) -> Optional[str]:
    """Map a fal ``image_size`` value to a Kontext ``aspect_ratio`` enum member.

    Returns None — after logging a warning — for values with no ratio
    information (e.g. the Seedream-only ``auto_*`` presets), so the caller
    omits the param and Kontext applies its own default geometry.
    """
    if isinstance(image_size, str):
        name = image_size.strip()
        if name in _IMAGE_SIZE_TO_ASPECT_RATIO:
            return _IMAGE_SIZE_TO_ASPECT_RATIO[name]
        if name in _KONTEXT_ASPECT_RATIOS:
            return name  # already an aspect-ratio string — pass through
    elif isinstance(image_size, dict):
        try:
            width = float(image_size.get("width"))
            height = float(image_size.get("height"))
        except (TypeError, ValueError):
            width = height = 0.0
        if width > 0 and height > 0:
            target = width / height
            # Numerically closest enum member; ties resolve in documented
            # (tuple) order, so the mapping is deterministic.
            return min(_KONTEXT_ASPECT_RATIOS, key=lambda r: abs(_aspect_ratio_decimal(r) - target))
    logger.warning(
        "image_size %r has no aspect_ratio mapping for endpoint %s; dropping (provider default applies)",
        image_size,
        endpoint_key,
    )
    return None


def _aspect_ratio_decimal(ratio: str) -> float:
    """Numeric value of a ``"W:H"`` aspect-ratio enum member."""
    width, _, height = ratio.partition(":")
    return int(width) / int(height)


#: Sentinel for "constraint check says omit this param".
_DROP = object()


def _apply_constraints(name: str, value: Any, spec: Dict[str, Any], endpoint_key: str) -> Any:
    """Validate ``value`` against the param's documented constraints.

    Returns the (possibly corrected) value, or the ``_DROP`` sentinel when the
    only safe option is to omit the parameter.
    """
    enum = spec.get("enum")
    if enum is not None:
        if value in enum:
            pass  # exact enum member, e.g. the string "4"
        elif str(value) in enum:
            # fal declares safety_tolerance as a STRING enum ("1".."6"); an
            # int 4 would fail schema validation, so emit the string form.
            return str(value)
        else:
            default = spec.get("default")
            if default is not None:
                logger.warning(
                    "param %s value %r not in %s for endpoint %s; falling back to default %r",
                    name,
                    value,
                    sorted(str(v) for v in enum),
                    endpoint_key,
                    default,
                )
                return default
            logger.warning(
                "param %s value %r not in %s for endpoint %s and no default is known; dropping",
                name,
                value,
                sorted(str(v) for v in enum),
                endpoint_key,
            )
            return _DROP

    if isinstance(value, bool):  # bool is an int subclass; leave it alone.
        return value

    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None or hi is not None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return value  # non-numeric: nothing to clamp here
        clamped = numeric
        if lo is not None and numeric < lo:
            clamped = lo
        if hi is not None and numeric > hi:
            clamped = hi
        if clamped != numeric:
            logger.warning(
                "param %s value %r outside [%s, %s] for endpoint %s; clamped to %s",
                name,
                value,
                lo,
                hi,
                endpoint_key,
                clamped,
            )
        if float(clamped).is_integer():
            return int(clamped)
        return clamped

    max_items = spec.get("max_items")
    if max_items is not None and isinstance(value, (list, tuple)) and len(value) > max_items:
        logger.warning(
            "param %s has %d items, endpoint %s allows %d; truncating",
            name,
            len(value),
            endpoint_key,
            max_items,
        )
        return list(value[:max_items])

    return value
