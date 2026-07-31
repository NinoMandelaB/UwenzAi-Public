// src/services/staffService.js
import { apiFetch } from "./apiClient";
import { API } from "../config/constants";

/**
 * List all staff users (admins) for assignment dropdown.
 * @returns {Promise<Object>} { status: "success", data: { users: [...] } }
 */
export async function listStaffUsers() {
  const response = await apiFetch(API.STAFF.LIST, {
    method: "GET",
    skipCsrf: true,
  });
  const data = await response.json();
  if (data.status !== "success") {
    const error = new Error(data.data || "Failed to load staff users.");
    error.status = response.status;
    error.code = data.data || "staff_list_failed";
    throw error;
  }
  return data;
}
