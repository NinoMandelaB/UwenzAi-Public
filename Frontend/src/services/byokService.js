// Frontend/src/services/byokService.js

import { apiFetch } from "./apiClient";

export const getBYOKConfig = () => apiFetch("/api/v1/auth/byok/");

export const saveBYOKConfig = (config) =>
  apiFetch("/api/v1/auth/byok/", {
    method: "POST",
    body: config,
  });

export const deleteBYOKConfig = () =>
  apiFetch("/api/v1/auth/byok/", { method: "DELETE" });

export const toggleBYOK = (isActive) =>
  apiFetch("/api/v1/auth/byok/toggle/", {
    method: "PATCH",
    body: { is_active: isActive },
  });
