// src/services/memoryService.js
import { API } from "../config/constants";
import { apiFetch } from "./apiClient";

/**
 * Clear all memory facts and summaries for a specific character.
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
