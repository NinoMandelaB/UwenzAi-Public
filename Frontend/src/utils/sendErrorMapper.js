/**
 * Turn a failed chat send into a localized, user-facing string.
 *
 * Same contract as ``localizeImageError``: the backend returns a stable code in
 * ``data`` (surfaced as ``error.code``), looked up under the ``errors.*``
 * namespace so English and Kiswahili both render. ``t()`` echoes the path back
 * when a key is missing, which is how an unmapped code is detected.
 *
 * The status fallbacks matter as much as the codes. DRF's own failures (a 404
 * from ``get_object_or_404``, a throttled 429) carry a prose ``detail`` rather
 * than a code, and a request blocked before it got a reply carries no body at
 * all — reporting all three as one generic message is what made the missing
 * ``Idempotency-Key`` CORS entry look like a server outage.
 */

const STATUS_FALLBACKS = {
  0: "errors.network_unreachable",
  401: "errors.guest_session_required",
  403: "errors.permission_denied",
  404: "errors.conversation_not_found",
  413: "errors.photo_too_large",
  429: "errors.rate_limited",
};

// Size and dimension refusals carry the allowed maximum in `details`
// (from the view or the 413 middleware); these are the {max} values for the
// localized messages. Defaults mirror the backend defaults so a bodyless
// 413 still shows a sensible number.
const DEFAULT_MAX_MB = 10;
const DEFAULT_MAX_MEGAPIXELS = 25;

function varsForCode(key, failure) {
  // Accepts either an error code or a translation path (the 413 status
  // fallback passes the path).
  const code = typeof key === "string" ? key.split(".").pop() : key;
  const details = failure?.payload?.details || {};
  if (code === "photo_too_large")
    return { max: details.max_mb ?? DEFAULT_MAX_MB };
  if (code === "photo_dimensions")
    return { max: details.max_megapixels ?? DEFAULT_MAX_MEGAPIXELS };
  return {};
}

export function localizeSendError(t, failure) {
  const code = failure?.code;
  if (code) {
    const path = `errors.${code}`;
    const localized = t(path, varsForCode(code, failure));
    if (localized !== path) return localized;
  }

  const status = failure?.status;
  if (STATUS_FALLBACKS[status])
    return t(
      STATUS_FALLBACKS[status],
      varsForCode(STATUS_FALLBACKS[status], failure),
    );
  if (typeof status === "number" && status >= 500)
    return t("errors.server_error");

  return t("chat.sendFailed");
}
