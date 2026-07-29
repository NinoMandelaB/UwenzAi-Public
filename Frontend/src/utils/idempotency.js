// src/utils/idempotency.js
//
// One user action — one send, one character, one picture — gets one key, and
// every network attempt for that action reuses it. The backend holds Sparks
// against the key, so a double click or a retry after a dropped response
// resolves to the same reservation and the same artifact instead of a second
// charge.
//
// The key must therefore outlive an individual fetch. Callers hold it for the
// duration of the action (a ref, usually) and drop it once the action reaches a
// terminal outcome, so the *next* action starts a genuinely new key.

export const IDEMPOTENCY_HEADER = "Idempotency-Key";

/**
 * Mint a key for a new user action.
 *
 * The backend only accepts canonical lowercase hyphenated UUIDs, which is what
 * both branches below produce. crypto.randomUUID needs a secure context, so the
 * fallback covers plain-HTTP development hosts and older mobile browsers.
 */
export function newOperationId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 1

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(
    "",
  );
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

/** Headers for a paid request, or none when the caller passed no key. */
export function idempotencyHeaders(operationId) {
  return operationId ? { [IDEMPOTENCY_HEADER]: operationId } : {};
}

/**
 * Key for the action described by ``actionKey``, minting one on first use.
 *
 * ``ref`` is a React ref the caller keeps for the lifetime of the component.
 * The key survives a failed attempt so a retry of the *same* action reuses it —
 * which matters most when the failure was a timeout, where the request may well
 * have reached the server and charged. Changing the action (different message
 * text, different prompt) mints a fresh key, so an edited retry is treated as
 * the new request it is rather than replaying the abandoned one.
 */
export function reuseOperationId(ref, actionKey) {
  if (!ref.current || ref.current.actionKey !== actionKey) {
    ref.current = { actionKey, operationId: newOperationId() };
  }
  return ref.current.operationId;
}

/** Forget the held key once its action has reached a terminal outcome. */
export function clearOperationId(ref) {
  ref.current = null;
}
