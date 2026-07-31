// src/services/apiClient.js

import { API, COOKIES } from "../config/constants";

let accessToken = null;
let refreshPromise = null;
let cachedCsrfToken = null;
let onSessionExpired = null; // <-- NEW

export function setAccessToken(token) {
  accessToken = token || null;
}

export function getAccessToken() {
  return accessToken;
}

export function clearAccessToken() {
  accessToken = null;
}

function isUnsafeMethod(method) {
  const normalized = (method || "GET").toUpperCase();
  return ["POST", "PUT", "PATCH", "DELETE"].includes(normalized);
}

function isFormData(value) {
  return value instanceof FormData;
}

/**
 * Register a callback that will be invoked when both an API call
 * and the subsequent token refresh fail with 401.
 */
export function setSessionExpiredCallback(callback) {
  onSessionExpired = callback;
}

/**
 * Fetch a fresh CSRF token from the server and cache it.
 * NEVER reads the cookie – always extracts the token from the response body.
 */
export async function ensureCsrfCookie() {
  if (cachedCsrfToken) {
    return cachedCsrfToken;
  }

  const response = await fetch(`${API.BASE_URL}${API.AUTH.CSRF}`, {
    method: "GET",
    credentials: "include",
  });

  if (!response.ok) {
    throw new Error("Failed to initialize CSRF.");
  }

  try {
    const data = await response.json();
    cachedCsrfToken = data?.data?.csrfToken || null;
  } catch {
    cachedCsrfToken = null;
  }

  return cachedCsrfToken;
}

/** Clear CSRF cache – call on logout. */
export function clearCsrfCache() {
  cachedCsrfToken = null;
}

async function performRefresh() {
  const csrfToken = await ensureCsrfCookie();
  if (!csrfToken) {
    clearAccessToken();
    return null;
  }

  const response = await fetch(`${API.BASE_URL}${API.AUTH.REFRESH}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "X-CSRFToken": csrfToken,
      "Content-Type": "application/json",
    },
  });

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok || data?.status !== "success") {
    clearAccessToken();
    return null;
  }

  const newAccessToken = data?.data?.access_token || null;
  if (!newAccessToken) {
    clearAccessToken();
    return null;
  }

  setAccessToken(newAccessToken);
  return {
    accessToken: newAccessToken,
    user: data?.data?.user || null,
    raw: data,
  };
}

export async function refreshAccessToken() {
  if (!refreshPromise) {
    refreshPromise = performRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

export async function apiFetch(path, options = {}) {
  const {
    skipAuthRefresh = false,
    skipAuthHeader = false,
    skipCsrf = false,
    csrfToken: explicitCsrfToken,
    headers = {},
    body,
    method = "GET",
    ...fetchOptions
  } = options;

  const normalizedMethod = method.toUpperCase();
  const usingFormData = isFormData(body);

  // Ensure we have a CSRF token (cached or fresh) for unsafe methods
  if (isUnsafeMethod(normalizedMethod) && !skipCsrf && !explicitCsrfToken) {
    await ensureCsrfCookie();
  }

  // Use the cached token or the explicit one
  const finalCsrfToken = explicitCsrfToken || cachedCsrfToken;

  const resolvedHeaders = {
    ...headers,
    ...(!skipAuthHeader && getAccessToken()
      ? { Authorization: `Bearer ${getAccessToken()}` }
      : {}),
    ...(isUnsafeMethod(normalizedMethod) && !skipCsrf && finalCsrfToken
      ? { "X-CSRFToken": finalCsrfToken }
      : {}),
  };

  if (!usingFormData && body && !resolvedHeaders["Content-Type"]) {
    resolvedHeaders["Content-Type"] = "application/json";
  }

  const resolvedBody =
    body &&
    !usingFormData &&
    resolvedHeaders["Content-Type"] === "application/json"
      ? JSON.stringify(body)
      : body;

  const makeRequest = (token) =>
    fetch(`${API.BASE_URL}${path}`, {
      ...fetchOptions,
      method: normalizedMethod,
      credentials: "include",
      body: resolvedBody,
      headers: {
        ...resolvedHeaders,
        ...(!skipAuthHeader && token
          ? { Authorization: `Bearer ${token}` }
          : {}),
      },
    });

  const tokenBeforeRequest = getAccessToken();
  let response = await makeRequest(tokenBeforeRequest);

  if (response.status === 401 && !skipAuthRefresh) {
    const refreshed = await refreshAccessToken();

    if (refreshed?.accessToken) {
      response = await makeRequest(refreshed.accessToken);
    } else if (tokenBeforeRequest && onSessionExpired) {
      // A session existed and could not be renewed, so it is truly expired.
      // Guests hold no token and never have a refresh cookie, so without this
      // check any 401 they hit (e.g. an authenticated-only endpoint reached by
      // mistake) would redirect them away and tear down their open chat.
      onSessionExpired();
    }
  }

  return response;
}
