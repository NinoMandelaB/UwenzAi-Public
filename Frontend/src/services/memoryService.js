// src/services/memoryService.js
//
// Read/modify access for the "Memory" popup (brain icon next to the calendar
// icon in the chat header). Backs the /api/v1/memory/editor/ endpoints — the
// same MemoryFact / MemoryRelation / ConversationSummary rows the automatic
// extraction pipeline writes. Editing here only corrects what the character
// knows; it never touches the relationship status or how memories are
// generated.

import { API } from "../config/constants";
import { apiFetch } from "./apiClient";
import {
  validateMemory,
  validateFact,
  validateRelation,
  validateSummary,
} from "./panelPayloads";

/**
 * Clear all memory facts and summaries for a specific character.
 * (Pre-existing "Reset memory" action in UserPreferences.)
 * @param {string} characterId - UUID of the character.
 * @returns {Promise<Object>} - { status: "success", data: { message, facts_deleted, summaries_deleted } }
 */
export async function clearMemoryForCharacter(characterId) {
  const url = API.MEMORY.CLEAR(characterId);
  const response = await apiFetch(url, {
    method: "DELETE",
    skipCsrf: true,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(data?.data || "Failed to clear memory.");
    error.status = response.status;
    error.payload = data;
    throw error;
  }

  return response.json();
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
 * Everything one character currently knows about the user:
 * { facts: [...], relations: [...], summary: {...} | null }
 * @param {string} characterId
 */
export async function listMemory(characterId, { page, pageSize = 50 } = {}) {
  const suffix = page ? `?page=${page}&page_size=${pageSize}` : "";
  const response = await apiFetch(
    `${API.MEMORY.EDITOR(characterId)}${suffix}`,
    {
      method: "GET",
    },
  );
  const data = await handleResponse(response);
  return validateMemory(data.data);
}

/** Add a fact the character should know (scoped to this character). */
export async function createMemoryFact(characterId, factText) {
  const response = await apiFetch(API.MEMORY.EDITOR_FACTS(characterId), {
    method: "POST",
    body: { fact_text: factText },
  });
  const data = await handleResponse(response);
  return validateFact(data.data);
}

/** Correct the text of an existing fact. */
export async function updateMemoryFact(factId, factText, preconditions = {}) {
  const response = await apiFetch(API.MEMORY.EDITOR_FACT(factId), {
    method: "PATCH",
    body: { fact_text: factText, ...preconditions },
  });
  const data = await handleResponse(response);
  return validateFact(data.data);
}

/** Permanently delete a fact. */
export async function deleteMemoryFact(factId, preconditions = {}) {
  const response = await apiFetch(API.MEMORY.EDITOR_FACT_DELETE(factId), {
    method: "DELETE",
    body: preconditions,
  });
  const data = await handleResponse(response);
  return data.data;
}

/** Add a knowledge-graph edge, e.g. ("my sister", "works at", "Safaricom"). */
export async function createMemoryRelation(
  characterId,
  { subject, predicate, object },
) {
  const response = await apiFetch(API.MEMORY.EDITOR_RELATIONS(characterId), {
    method: "POST",
    body: { subject, predicate, object },
  });
  const data = await handleResponse(response);
  return validateRelation(data.data);
}

/** Edit an existing edge (all three parts). */
export async function updateMemoryRelation(
  relationId,
  { subject, predicate, object },
  preconditions = {},
) {
  const response = await apiFetch(API.MEMORY.EDITOR_RELATION(relationId), {
    method: "PATCH",
    body: { subject, predicate, object, ...preconditions },
  });
  const data = await handleResponse(response);
  return validateRelation(data.data);
}

/** Permanently delete an edge. */
export async function deleteMemoryRelation(relationId, preconditions = {}) {
  const response = await apiFetch(
    API.MEMORY.EDITOR_RELATION_DELETE(relationId),
    {
      method: "DELETE",
      body: preconditions,
    },
  );
  const data = await handleResponse(response);
  return data.data;
}

/** Edit the current rolling conversation summary. */
export async function updateMemorySummary(
  characterId,
  summaryText,
  preconditions = {},
) {
  const response = await apiFetch(API.MEMORY.EDITOR_SUMMARY(characterId), {
    method: "PATCH",
    body: { summary_text: summaryText, ...preconditions },
  });
  const data = await handleResponse(response);
  return validateSummary(data.data, false);
}

/** Delete the current summary (a new one regenerates automatically later). */
export async function deleteMemorySummary(characterId, preconditions = {}) {
  const response = await apiFetch(
    API.MEMORY.EDITOR_SUMMARY_DELETE(characterId),
    {
      method: "DELETE",
      body: preconditions,
    },
  );
  const data = await handleResponse(response);
  return data.data;
}
