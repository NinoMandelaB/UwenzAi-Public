import { API } from "../config/constants";

/**
 * Resolve an attachment/media URL for rendering.
 *
 * Absolute http(s) URLs (signed attachment URLs, gallery images) and
 * browser-local `blob:`/`data:` URLs (optimistic send previews) pass through
 * unchanged. Only relative API paths get the API base prefixed — prefixing a
 * blob: URL once produced GET <backend>/blob:https://... 404s.
 */
export function resolveMediaUrl(url) {
  if (!url) return url;
  if (/^(https?:|blob:|data:)/i.test(url)) return url;
  const base = API.BASE_URL ? API.BASE_URL.replace(/\/$/, "") : "";
  return `${base}${url.startsWith("/") ? "" : "/"}${url}`;
}
