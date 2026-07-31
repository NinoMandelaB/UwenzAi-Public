// src/services/messageFlagService.js
import { API } from "../config/constants";
import { apiFetch } from "./apiClient";

export async function getFlaggedMessageIds(conversationId) {
  const response = await apiFetch(
    API.MESSAGE_FLAGS.FOR_CONVERSATION(conversationId),
    { method: "GET" },
  );
  return handleResponse(response);
}

/* ------------------------------------------------------------------ */
/*  Shared error‑handling helpers (same as other services)             */
/* ------------------------------------------------------------------ */
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

/* ------------------------------------------------------------------ */
/*  Message Flagging                                                   */
/* ------------------------------------------------------------------ */

/**
 * Submit a flag on a message.
 *
 * Guests are identified by their credential cookie; a guest session id in the
 * body would let anyone flag messages as somebody else.
 *
 * @param {Object} params
 * @param {string} params.message_id - UUID of the message to flag
 * @param {string} params.reason - Reason for the flag
 */
export async function submitMessageFlag({ message_id, reason }) {
  const response = await apiFetch(API.MESSAGE_FLAGS.SUBMIT, {
    method: "POST",
    body: { message_id, reason },
  });
  return handleResponse(response);
}

/**
 * List all message flags (admin only).
 */
/**
 * List all message flags (admin only).
 * @param {number} page - Page number (default 1)
 * @param {number} pageSize - Items per page (default 20)
 * @param {object} filters - Optional filters (e.g. { reviewed: false })
 */
export async function listMessageFlags(page = 1, pageSize = 20, filters = {}) {
  let url = `${API.MESSAGE_FLAGS.ADMIN_LIST}?page=${page}&page_size=${pageSize}`;
  if (filters.reviewed !== undefined) {
    url += `&reviewed=${filters.reviewed}`;
  }
  const response = await apiFetch(url, { method: "GET" });
  return handleResponse(response);
}

/**
 * Review a message flag (admin only).
 * @param {string} flagId - UUID of the flag
 * @param {Object} params
 * @param {string} [params.resolution_note] - Optional note
 * @param {boolean} [params.dismiss] - Whether to dismiss the flag
 */
export async function reviewMessageFlag(
  flagId,
  { resolution_note, dismiss } = {},
) {
  const response = await apiFetch(API.MESSAGE_FLAGS.ADMIN_REVIEW(flagId), {
    method: "POST",
    body: { resolution_note, dismiss },
  });
  return handleResponse(response);
}
