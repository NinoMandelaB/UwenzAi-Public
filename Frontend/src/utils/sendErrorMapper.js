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
  429: "errors.rate_limited",
};

export function localizeSendError(t, failure) {
  const code = failure?.code;
  if (code) {
    const path = `errors.${code}`;
    const localized = t(path);
    if (localized !== path) return localized;
  }

  const status = failure?.status;
  if (STATUS_FALLBACKS[status]) return t(STATUS_FALLBACKS[status]);
  if (typeof status === "number" && status >= 500)
    return t("errors.server_error");

  return t("chat.sendFailed");
}
