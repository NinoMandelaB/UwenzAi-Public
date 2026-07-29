// src/utils/session.js
//
// Guest identity is server-minted and lives in an HttpOnly cookie, so nothing
// here can read it — and neither can any script on the page. That is the point:
// the old client-generated UUID in localStorage was a bearer credential that
// any XSS, extension or curious user could lift and replay to take over
// somebody else's conversations, sparks and purchase history.
//
// The only client-side responsibilities left are to make sure the cookie exists
// before the first guest API call, and to hand the one legacy localStorage ID
// still in the wild back to the server exactly once.

import { API } from "../config/constants";
import { apiFetch } from "../services/apiClient";

const LEGACY_GUEST_ID_KEY = "guest_session_id";
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

let bootstrapPromise = null;

function readLegacyGuestId() {
  let value = null;
  try {
    value = localStorage.getItem(LEGACY_GUEST_ID_KEY);
  } catch {
    return null;
  }
  return value && UUID_PATTERN.test(value.trim()) ? value.trim() : null;
}

function forgetLegacyGuestId() {
  try {
    localStorage.removeItem(LEGACY_GUEST_ID_KEY);
  } catch {
    // A browser that refuses localStorage cannot be replaying a legacy ID.
  }
}

/**
 * Trade the legacy localStorage ID for a server-minted credential, once.
 *
 * Only cleared when the server gives a verdict that will not change on retry:
 * migrated, unknown, or already taken. A closed migration window (410) or a
 * network failure leaves the key in place so the guest's data is not orphaned
 * by a transient condition.
 */
async function migrateLegacyGuestSession() {
  const legacyId = readLegacyGuestId();
  if (!legacyId) {
    return;
  }

  let response;
  try {
    response = await apiFetch(API.GUEST.MIGRATE_SESSION, {
      method: "POST",
      body: { legacy_session_id: legacyId },
      skipAuthRefresh: true,
    });
  } catch {
    return;
  }

  if (response.ok || response.status === 404 || response.status === 409) {
    forgetLegacyGuestId();
  }
}

/**
 * Make sure this browser has a usable guest credential cookie.
 *
 * Safe to call from anywhere: concurrent callers share one in-flight request,
 * so a page that mounts several guest-facing components does not mint several
 * identities.
 */
export function ensureGuestSession() {
  if (!bootstrapPromise) {
    bootstrapPromise = (async () => {
      await migrateLegacyGuestSession();
      try {
        await apiFetch(API.GUEST.ENSURE_SESSION, {
          method: "POST",
          skipAuthRefresh: true,
        });
      } catch {
        // Offline or backend down. The next guest action retries.
        bootstrapPromise = null;
      }
    })();
  }
  return bootstrapPromise;
}

/** Forget the cached bootstrap so the next guest action re-establishes it. */
export function resetGuestSession() {
  bootstrapPromise = null;
}

/**
 * Obtain a short-lived, single-use ticket for a guest WebSocket.
 *
 * WebSocket URLs end up in proxy and server access logs, so the guest
 * credential itself must never appear in one. The ticket is worth a few seconds
 * and one connection, and is bound to this channel and conversation.
 */
export async function issueGuestWsTicket(channel, conversationId = null) {
  await ensureGuestSession();

  const body = { channel };
  if (conversationId) {
    body.conversation_id = conversationId;
  }

  const response = await apiFetch(API.GUEST.WS_TICKET, {
    method: "POST",
    body,
    skipAuthRefresh: true,
  });

  if (!response.ok) {
    return null;
  }

  const data = await response.json().catch(() => null);
  return data?.data?.ticket || null;
}
