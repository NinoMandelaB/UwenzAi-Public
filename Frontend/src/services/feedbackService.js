import { API } from "../config/constants";
import { apiFetch } from "./apiClient";

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

/**
 * Submit user feedback
 * @param {Object} payload - { message, rating (optional) }
 */
export async function submitFeedback(payload) {
  const response = await apiFetch(API.FEEDBACK.SUBMIT, {
    method: "POST",
    body: {
      message: payload.message,
      rating: payload.rating,
    },
  });
  return handleResponse(response);
}

/**
 * List all feedback (admin only)
 */
export async function listFeedback() {
  const response = await apiFetch(API.FEEDBACK.ADMIN_LIST, {
    method: "GET",
  });
  return handleResponse(response);
}

/**
 * Mark feedback as read (admin only)
 * @param {string} feedbackId - UUID of the feedback
 */
export async function markFeedbackAsRead(feedbackId) {
  const response = await apiFetch(API.FEEDBACK.ADMIN_MARK_READ(feedbackId), {
    method: "POST",
  });
  return handleResponse(response);
}

/**
 * Delete feedback (admin only)
 * @param {string} feedbackId - UUID of the feedback
 */
export async function deleteFeedback(feedbackId) {
  const response = await apiFetch(API.FEEDBACK.ADMIN_DELETE(feedbackId), {
    method: "DELETE",
  });
  return handleResponse(response);
}
