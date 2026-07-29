// src/services/supportService.js
import { apiFetch } from "./apiClient";
import { API } from "../config/constants";

/**
 * List support tickets.
 * @param {Object} filters - optional filters: { status, priority, assigned_to }
 * @returns {Promise<Object>}
 */
export const listTickets = async (filters = {}) => {
  const query = new URLSearchParams(filters).toString();
  const url = API.SUPPORT.TICKETS + (query ? `?${query}` : "");
  const response = await apiFetch(url, { method: "GET", skipCsrf: true });
  return response.json();
};

/**
 * Create a new support ticket.
 * @param {string} title
 * @param {string} description
 * @param {string} priority - 'low', 'medium', 'high', 'urgent'
 * @returns {Promise<Object>}
 */
// src/services/supportService.js

export const createTicket = async (title, description, priority = "medium") => {
  const response = await apiFetch(API.SUPPORT.CREATE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, description, priority }),
  });
  return response.json();
};

/**
 * Retrieve a single ticket with full threaded messages.
 * @param {string} ticketId - UUID
 * @returns {Promise<Object>}
 */
export const getTicket = async (ticketId) => {
  const url = API.SUPPORT.TICKET_DETAIL(ticketId);
  const response = await apiFetch(url, { method: "GET", skipCsrf: true });
  return response.json();
};

/**
 * Add a reply to a ticket.
 * @param {string} ticketId - UUID
 * @param {string} content - message text
 * @param {string|null} parentMessageId - UUID of the message being replied to (for threading)
 * @param {boolean} isInternal - if true, only admins see this note
 * @returns {Promise<Object>}
 */
export const replyToTicket = async (
  ticketId,
  content,
  parentMessageId = null,
  isInternal = false,
) => {
  const url = API.SUPPORT.REPLY(ticketId);
  const response = await apiFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content,
      parent_message_id: parentMessageId,
      is_internal: isInternal,
    }),
  });
  return response.json();
};

/**
 * Get the total number of unread messages across all tickets.
 * @returns {Promise<Object>} { count: number }
 */
export const getUnreadCount = async () => {
  const response = await apiFetch(API.SUPPORT.UNREAD_COUNT, {
    method: "GET",
    skipCsrf: true,
  });
  return response.json();
};

/**
 * Mark all messages in a ticket as read.
 * @param {string} ticketId - UUID
 * @returns {Promise<Object>}
 */
export const markTicketRead = async (ticketId) => {
  const url = API.SUPPORT.MARK_READ(ticketId);
  const response = await apiFetch(url, {
    method: "POST",
    skipCsrf: true,
  });
  return response.json();
};

/**
 * Update ticket status (admin only).
 * @param {string} ticketId - UUID
 * @param {string} status - 'open', 'in_progress', 'resolved', 'closed'
 * @param {string|null} assignedToId - UUID of the admin to assign
 * @returns {Promise<Object>}
 */
export const updateTicketStatus = async (
  ticketId,
  status,
  assignedToId = null,
) => {
  const url = API.SUPPORT.UPDATE_STATUS(ticketId);
  const body = {};
  if (status !== undefined && status !== null) body.status = status;
  // Include assigned_to_id if provided (even if empty string)
  if (assignedToId !== undefined && assignedToId !== null)
    body.assigned_to_id = assignedToId;
  const response = await apiFetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return response.json();
};
