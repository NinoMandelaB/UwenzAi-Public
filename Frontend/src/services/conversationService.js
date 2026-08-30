// src/services/conversationService.js

import { API } from "../config/constants";
import { apiFetch, getAccessToken } from "./apiClient";
import { refreshAccessToken } from "./authService";
import { idempotencyHeaders } from "../utils/idempotency";

// ---------- Helpers ----------

async function ensureAuth() {
  if (!getAccessToken()) {
    try {
      await refreshAccessToken();
    } catch (e) {
      console.warn(
        "User is not logged in or refresh failed; proceeding as guest.",
      );
    }
  }
}

function createApiError({
  message = "Request failed.",
  status = null,
  payload = null,
  code = null,
}) {
  const error = new Error(message);
  error.status = status;
  error.payload = payload;
  error.code = code;
  return error;
}

async function parseJsonSafely(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function extractErrorMessage(payload) {
  if (!payload) return { message: "Request failed.", code: null };
  if (typeof payload?.data === "string")
    return { message: payload.data, code: payload.data };
  if (typeof payload?.data?.code === "string")
    return { message: payload.data.code, code: payload.data.code };
  if (Array.isArray(payload?.data?.errors) && payload.data.errors.length > 0) {
    return {
      message: payload.data.errors.join(" "),
      code: payload?.data?.code || null,
    };
  }
  if (typeof payload?.message === "string")
    return { message: payload.message, code: payload.message };
  if (typeof payload?.detail === "string")
    return { message: payload.detail, code: payload.detail };
  return { message: "Request failed.", code: null };
}

async function handleResponse(response) {
  const data = await parseJsonSafely(response);
  if (!response.ok || data?.status !== "success") {
    const extracted = extractErrorMessage(data);
    throw createApiError({
      message: extracted.message,
      code: extracted.code,
      status: response.status,
      payload: data,
    });
  }
  return data;
}

// ---------- Conversation CRUD ----------

export async function startConversation(payload) {
  // payload: { character_id }
  await ensureAuth();
  const response = await apiFetch(API.CONVERSATIONS.START, {
    method: "POST",
    body: payload,
  });
  return response.json();
}

// Paid endpoints take an operationId: one per user action, reused across
// retries of that action, so the backend charges it exactly once. See
// src/utils/idempotency.js.

export async function sendMessage(conversationId, payload, operationId) {
  // payload: { content }
  await ensureAuth();

  let response;
  try {
    response = await apiFetch(API.CONVERSATIONS.SEND_MESSAGE(conversationId), {
      method: "POST",
      body: payload,
      headers: idempotencyHeaders(operationId),
    });
  } catch {
    // fetch only rejects when the request never got an HTTP reply: the user is
    // offline, or a preflight/CORS policy blocked it. Both need naming, because
    // reporting them as a generic failure hides a misconfigured API from us.
    throw createApiError({
      message: "network_unreachable",
      code: "network_unreachable",
      status: 0,
    });
  }

  return handleResponse(response);
}

export async function generateChatImage(conversationId, payload, operationId) {
  // payload: { prompt }
  await ensureAuth();
  const response = await apiFetch(
    API.CONVERSATIONS.GENERATE_IMAGE(conversationId),
    {
      method: "POST",
      body: payload,
      headers: idempotencyHeaders(operationId),
    },
  );
  return handleResponse(response);
}

// Character-initiated photo offers: the offer message carries its own
// operation key server-side (offer:<uuid>), so the client does not mint an
// idempotency key here — retrying the accept replays the same reservation.

export async function acceptPhotoOffer(conversationId, messageId) {
  await ensureAuth();
  const response = await apiFetch(
    API.CONVERSATIONS.ACCEPT_PHOTO_OFFER(conversationId, messageId),
    { method: "POST" },
  );
  return handleResponse(response);
}

export async function declinePhotoOffer(conversationId, messageId) {
  await ensureAuth();
  const response = await apiFetch(
    API.CONVERSATIONS.DECLINE_PHOTO_OFFER(conversationId, messageId),
    { method: "POST" },
  );
  return handleResponse(response);
}

// Guests are identified by their HttpOnly credential cookie, which apiFetch
// sends on every request, so none of these take a session id any more.

export async function listConversations() {
  const response = await apiFetch(API.CONVERSATIONS.LIST, { method: "GET" });
  const data = await handleResponse(response);
  return data;
}

export async function getUnreadSummary() {
  const response = await apiFetch(API.CONVERSATIONS.UNREAD, { method: "GET" });
  const data = await handleResponse(response);
  return data.data;
}

export async function markConversationRead(conversationId) {
  const response = await apiFetch(API.CONVERSATIONS.MARK_READ(conversationId), {
    method: "POST",
    body: {},
  });
  const data = await handleResponse(response);
  return data.data;
}

export async function resetConversation(conversationId) {
  const url = API.CONVERSATIONS.RESET(conversationId);
  const response = await apiFetch(url, { method: "DELETE" });
  return response.json();
}

export async function deleteMessage(conversationId, messageId) {
  const url = API.CONVERSATIONS.DELETE_MESSAGE(conversationId, messageId);
  const response = await apiFetch(url, { method: "DELETE" });
  return response.json();
}

// ---------- Per-chat search & reasoning-effort toggles ----------

/**
 * Get the per-chat web-search and reasoning-effort (Fast/Deep) toggle state.
 * @param {string} conversationId - UUID of the conversation.
 * @returns {Promise<Object>} - { web_search_enabled: boolean, reasoning_effort: "fast" | "deep" }
 */
export async function getConversationSettings(conversationId) {
  const url = API.CONVERSATIONS.SETTINGS(conversationId);
  const response = await apiFetch(url, { method: "GET" });
  const data = await handleResponse(response);
  return data.data;
}

/**
 * Update the per-chat web-search and/or reasoning-effort toggle state.
 * Either field may be omitted to leave it unchanged. Takes effect starting
 * with the conversation's next message.
 * @param {string} conversationId - UUID of the conversation.
 * @param {Object} updates
 * @param {boolean} [updates.webSearchEnabled]
 * @param {"fast"|"deep"} [updates.reasoningEffort]
 * @returns {Promise<Object>} - { web_search_enabled: boolean, reasoning_effort: "fast" | "deep" }
 */
export async function updateConversationSettings(
  conversationId,
  { webSearchEnabled, reasoningEffort } = {},
) {
  const url = API.CONVERSATIONS.SETTINGS(conversationId);
  const body = {};
  if (webSearchEnabled !== undefined)
    body.web_search_enabled = webSearchEnabled;
  if (reasoningEffort !== undefined) body.reasoning_effort = reasoningEffort;

  const response = await apiFetch(url, {
    method: "PATCH",
    body,
  });
  const data = await handleResponse(response);
  return data.data;
}

// ---------- Message history ----------

export async function listMessages(conversationId, options = {}) {
  const { before, limit, signal } = options;
  const params = new URLSearchParams();
  if (before) params.set("before", before);
  if (limit) params.set("limit", String(limit));
  const url = `${API.CONVERSATIONS.MESSAGE_HISTORY(conversationId)}${params.toString() ? `?${params.toString()}` : ""}`;

  const response = await apiFetch(url, { method: "GET", signal });
  const data = await handleResponse(response);
  return data;
}

// ---------- Proactive Chat (Interactive Chatting) ----------

/**
 * Get proactive chat preference for a character.
 * @param {string} characterId - UUID of the character.
 * @returns {Promise<Object>} - { enabled: boolean, interval_hours: number }
 */
export async function getProactivePreference(characterId) {
  const url = API.PROACTIVE_CHAT.PREFERENCE(characterId);
  const response = await apiFetch(url, { method: "GET" });
  const data = await handleResponse(response);
  return data.data;
}

/**
 * Update proactive chat preference for a character.
 * @param {string} characterId - UUID of the character.
 * @param {boolean} enabled - Enable/disable proactive chat.
 * @param {number} intervalHours - Interval in hours (6–168).
 * @returns {Promise<Object>} - { enabled: boolean, interval_hours: number }
 */
export async function updateProactivePreference(
  characterId,
  enabled,
  intervalHours,
) {
  const url = API.PROACTIVE_CHAT.UPDATE(characterId);
  const response = await apiFetch(url, {
    method: "PUT",
    body: JSON.stringify({
      enabled,
      interval_hours: intervalHours,
    }),
  });
  const data = await handleResponse(response);
  return data.data;
}
