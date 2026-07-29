/**
 * Turn an image-generation failure into a localized, user-facing string.
 *
 * The backend returns a stable error code in ``data`` (surfaced as
 * ``error.code``). We look that code up under the ``errors.*`` namespace so both
 * English and Kiswahili render properly. ``t()`` echoes the path back when a key
 * is missing, which is how we detect an unmapped code and fall back to the
 * generic failure message rather than showing a raw key to the user.
 */
export function localizeImageError(t, failure) {
  const code = failure?.code;
  if (code) {
    const path = `errors.${code}`;
    const localized = t(path);
    if (localized !== path) return localized;
  }
  return failure?.message || t("chat.imageGenFailed");
}
