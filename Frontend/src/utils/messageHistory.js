// src/utils/messageHistory.js

/**
 * Pure helpers for assembling a chat transcript out of paged history and live
 * WebSocket messages.
 *
 * Both arrive independently: a page of older history can land while the AI is
 * replying, and the optimistic echo of a message the user just sent has no
 * server id yet. Keeping the merge rules here — rather than inside the hooks —
 * makes them reviewable and reusable by both chat surfaces.
 */

/** Shape one server history row into the object the chat views render. */
export function normalizeHistoryMessage(raw) {
  return {
    message_id: raw.message_id,
    sender_type: raw.sender_type,
    content: raw.content,
    image_url: raw.image_url || null,
    // User-sent photo attachments: [{ attachment_id, url, content_type }]
    // with short-lived signed URLs (fresh per history response).
    attachments: Array.isArray(raw.attachments) ? raw.attachments : [],
    created_at: raw.created_at,
    reply_to: raw.reply_to || null,
    code: raw.code || undefined,
    type: raw.type || "",
    // Present only on character-initiated photo offers (type "photo_offer"):
    // { prompt, cost, status } so a reloaded page can re-open the popup.
    offer: raw.offer || null,
  };
}

/**
 * Prepend an older page in front of the transcript, dropping anything already
 * present. Pages are chronological and strictly older than what is loaded, so
 * the result stays chronological without a re-sort.
 */
export function mergeOlderMessages(current, older) {
  if (!older || older.length === 0) return current;

  const known = new Set(
    current.map((message) => message.message_id).filter(Boolean),
  );
  const missing = older.filter(
    (message) => !message.message_id || !known.has(message.message_id),
  );

  return missing.length === 0 ? current : [...missing, ...current];
}

/**
 * Append a live message. Messages without an id (ephemeral system notices, the
 * optimistic echo of a send) are always kept — there is nothing to match them
 * against, and dropping them would lose the notice.
 */
export function appendLiveMessage(current, incoming) {
  if (!incoming) return current;
  if (
    incoming.message_id &&
    current.some((message) => message.message_id === incoming.message_id)
  ) {
    return current;
  }
  return [...current, incoming];
}

/** Read pagination metadata, tolerating a server that predates it. */
export function readPagination(payload) {
  const pagination = payload?.pagination;
  return {
    hasMore: Boolean(pagination?.has_more),
    nextBefore: pagination?.next_before || null,
  };
}
