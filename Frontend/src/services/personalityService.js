import { API } from "../config/constants";
import { apiFetch } from "./apiClient";

async function parseJsonSafely(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function deletePersonality(personalityId) {
  const response = await apiFetch(API.PERSONALITIES.DELETE(personalityId), {
    method: "DELETE",
  });
  const data = await response.json().catch(() => null);
  return data;
}

export async function listPersonalities() {
  const response = await apiFetch(API.PERSONALITIES.LIST, {
    method: "GET",
  });
  const data = await parseJsonSafely(response);
  return data;
}

export async function createPersonality(name, description = "") {
  const response = await apiFetch(API.PERSONALITIES.CREATE, {
    method: "POST",
    body: { name, description },
  });
  const data = await response.json().catch(() => null);
  return data;
}
