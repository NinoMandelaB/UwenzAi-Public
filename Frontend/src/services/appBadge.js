// src/services/appBadge.js

/**
 * App-icon badge for the installed PWA (Badging API).
 *
 * Android and desktop Chrome show the count on the launcher/taskbar icon;
 * iOS does too once notification permission was granted. Where the API does
 * not exist every call is a silent no-op, and a rejected promise (e.g. the
 * app is not installed) is swallowed — the in-app badges stay the source of
 * truth, the icon is only a mirror.
 */
export async function syncAppBadge(count) {
  if (!("setAppBadge" in navigator)) return;
  try {
    if (count > 0) {
      await navigator.setAppBadge(count);
    } else {
      await navigator.clearAppBadge();
    }
  } catch {
    // Best-effort by design — see the file comment.
  }
}
