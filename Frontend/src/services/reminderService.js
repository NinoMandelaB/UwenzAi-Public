// src/services/reminderService.js
//
// CRUD for reminders, backing the calendar/task icon + popup in the chat UI.
// This is a UI-driven way to manage the same Reminder rows the
// create_reminder/update_reminder/cancel_reminder chat tools operate on
// server-side -- both paths share the same model and lifecycle.

import { API } from "../config/constants";
import { apiFetch } from "./apiClient";
import { validateReminder, validateReminders } from "./panelPayloads";

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
 * List every pending reminder for the current user, across all chats.
 * @returns {Promise<Array<Object>>}
 */
export async function listReminders() {
  const response = await apiFetch(API.REMINDERS.LIST, { method: "GET" });
  const data = await handleResponse(response);
  return validateReminders(data.data);
}

/**
 * Create a new reminder tied to a conversation.
 * @param {Object} payload
 * @param {string} payload.conversationId
 * @param {string} payload.reminderText
 * @param {string} payload.dueDate - "YYYY-MM-DD", interpreted in the user's stored timezone.
 * @param {string} payload.dueTime - "HH:MM", interpreted in the user's stored timezone.
 * @returns {Promise<Object>}
 */
export async function createReminder({
  conversationId,
  reminderText,
  dueDate,
  dueTime,
}) {
  const response = await apiFetch(API.REMINDERS.CREATE, {
    method: "POST",
    body: {
      conversation_id: conversationId,
      reminder_text: reminderText,
      due_date: dueDate,
      due_time: dueTime,
    },
  });
  const data = await handleResponse(response);
  return validateReminder(data.data);
}

/**
 * Update an existing pending reminder. Only provided fields change.
 * @param {string} reminderId
 * @param {Object} updates
 * @param {string} [updates.reminderText]
 * @param {string} [updates.dueDate] - "YYYY-MM-DD"
 * @param {string} [updates.dueTime] - "HH:MM"
 * @returns {Promise<Object>}
 */
export async function updateReminder(
  reminderId,
  { reminderText, dueDate, dueTime } = {},
) {
  const body = {};
  if (reminderText !== undefined) body.reminder_text = reminderText;
  if (dueDate !== undefined) body.due_date = dueDate;
  if (dueTime !== undefined) body.due_time = dueTime;

  const response = await apiFetch(API.REMINDERS.UPDATE(reminderId), {
    method: "PATCH",
    body,
  });
  const data = await handleResponse(response);
  return validateReminder(data.data);
}

/**
 * Cancel (delete) a pending reminder.
 * @param {string} reminderId
 * @returns {Promise<Object>} - { reminder_id }
 */
export async function deleteReminder(reminderId) {
  const response = await apiFetch(API.REMINDERS.DELETE(reminderId), {
    method: "DELETE",
  });
  const data = await handleResponse(response);
  return data.data;
}
