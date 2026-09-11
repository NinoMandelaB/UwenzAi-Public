// src/services/authService.js

import { API, STORAGE_KEYS } from "../config/constants";
import {
  ensureCsrfCookie,
  getAccessToken,
  apiFetch,
  getCachedCsrfToken,
  getAuthSessionGeneration,
  refreshAccessToken as renewAccessToken,
} from "./apiClient";
import { unsubscribeFromPush, unsubscribePushLocally } from "./pushService";
import { boundedTask } from "../utils/boundedTask";

// -----------------------------------------------------------------
//  Helpers
// -----------------------------------------------------------------
function storeUserData(data) {
  const user = data?.user;
  const spark_pool = data?.spark_pool;

  if (user) {
    localStorage.setItem(STORAGE_KEYS.AUTH_USER, JSON.stringify(user));
  } else {
    localStorage.removeItem(STORAGE_KEYS.AUTH_USER);
  }

  if (spark_pool) {
    localStorage.setItem(STORAGE_KEYS.SPARK_POOL, JSON.stringify(spark_pool));
  } else {
    localStorage.removeItem(STORAGE_KEYS.SPARK_POOL);
  }
}

function clearStoredUserData() {
  localStorage.removeItem(STORAGE_KEYS.AUTH_USER);
  localStorage.removeItem(STORAGE_KEYS.SPARK_POOL);
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

function extractErrorMessage(payload) {
  if (!payload) {
    return { message: "Request failed.", code: null };
  }

  if (typeof payload?.data === "string") {
    return { message: payload.data, code: payload.data };
  }

  if (typeof payload?.data?.code === "string") {
    return {
      message: payload.data.code,
      code: payload.data.code,
    };
  }

  if (Array.isArray(payload?.data?.errors) && payload.data.errors.length > 0) {
    return {
      message: payload.data.errors.join(" "),
      code: payload?.data?.code || null,
    };
  }

  if (typeof payload?.message === "string") {
    return { message: payload.message, code: payload.message };
  }

  if (typeof payload?.detail === "string") {
    return { message: payload.detail, code: payload.detail };
  }

  const flattenedFieldErrors = flattenFieldErrors(payload);
  if (flattenedFieldErrors.length > 0) {
    return {
      message: flattenedFieldErrors.join(" "),
      code: null,
    };
  }

  return { message: "Request failed.", code: null };
}

function flattenFieldErrors(value, path = "") {
  if (!value || typeof value !== "object") {
    return [];
  }

  const messages = [];

  for (const [key, current] of Object.entries(value)) {
    const label = path || key;

    if (Array.isArray(current)) {
      for (const item of current) {
        if (typeof item === "string") {
          messages.push(`${label}: ${item}`);
        } else if (item && typeof item === "object") {
          messages.push(...flattenFieldErrors(item, label));
        }
      }
      continue;
    }

    if (typeof current === "string") {
      messages.push(`${label}: ${current}`);
      continue;
    }

    if (current && typeof current === "object") {
      messages.push(...flattenFieldErrors(current, key));
    }
  }

  return messages;
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

// -----------------------------------------------------------------
//  Enhanced response handler – throws consistent ApiError
// -----------------------------------------------------------------
async function handleFetchResponse(response) {
  const data = await parseJsonSafely(response);

  if (!response.ok || data?.status !== "success") {
    // Special handling for 429 Too Many Requests
    if (response.status === 429) {
      let retryAfter = response.headers.get("Retry-After");
      let message =
        "Too many attempts. Please wait a moment before trying again.";
      if (retryAfter) {
        message = `Too many attempts. Please try again in ${retryAfter} seconds.`;
      }
      throw createApiError({
        message,
        code: "rate_limited",
        status: response.status,
        payload: data,
      });
    }

    const extracted = extractErrorMessage(data);
    throw createApiError({
      message: extracted.message,
      code: extracted.code || `http_${response.status}`,
      status: response.status,
      payload: data,
    });
  }

  return data;
}

// -----------------------------------------------------------------
//  Core request helpers
// -----------------------------------------------------------------
async function postWithCsrf(endpoint, payload, options = {}) {
  const { accessToken = null } = options;

  const csrfToken = await ensureCsrfCookie();

  const headers = {
    "Content-Type": "application/json",
    "X-CSRFToken": csrfToken,
  };

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${API.BASE_URL}${endpoint}`, {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(payload),
  });

  return handleFetchResponse(response);
}

async function getWithCredentials(endpoint, options = {}) {
  const { accessToken = null } = options;

  const headers = {
    Accept: "application/json",
  };

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${API.BASE_URL}${endpoint}`, {
    method: "GET",
    credentials: "include",
    headers,
  });

  return handleFetchResponse(response);
}

// -----------------------------------------------------------------
//  Public API
// -----------------------------------------------------------------
export async function deleteAccount() {
  const csrfToken = await ensureCsrfCookie();
  const accessToken = getAccessToken(); // ← read from memory

  const headers = {
    "Content-Type": "application/json",
    "X-CSRFToken": csrfToken,
  };

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${API.BASE_URL}${API.AUTH.DELETE_ACCOUNT}`, {
    method: "DELETE",
    credentials: "include",
    headers,
  });

  const data = await handleFetchResponse(response);
  await unsubscribePushLocally().catch(() => {});
  clearStoredUserData(); // clears user & spark_pool from localStorage
  return data;
}

export async function signupUser(payload) {
  const data = await postWithCsrf(API.AUTH.SIGNUP, payload);
  clearStoredUserData();
  return data;
}

async function authenticate(
  endpoint,
  payload,
  { signal, expectedUserId } = {},
) {
  const generation = getAuthSessionGeneration();
  const assertCurrent = () => {
    if (
      signal?.aborted ||
      generation !== getAuthSessionGeneration() ||
      (expectedUserId &&
        (!getAccessToken() || getStoredUser()?.user_id !== expectedUserId))
    )
      throw new DOMException("Authentication attempt expired.", "AbortError");
  };
  assertCurrent();
  const response = await apiFetch(endpoint, {
    method: "POST",
    body: payload,
    signal,
    skipAuthHeader: !expectedUserId,
    // A link must never retry with another session's bearer.
    skipAuthRefresh: true,
  });
  assertCurrent();
  const data = await handleFetchResponse(response);
  assertCurrent();
  if (expectedUserId) {
    if (data?.data?.user?.user_id !== expectedUserId) {
      throw createApiError({
        code: "google_link_confirmation_required",
        message: "google_link_confirmation_required",
      });
    }
    if (data?.data?.linked !== true) {
      throw new Error("Invalid Google connection response.");
    }
    // Connecting a provider is not a login: do not replace tokens or profile.
  } else {
    if (!data?.data?.user?.user_id || !data?.data?.access_token) {
      throw new Error("Invalid sign-in response.");
    }
    storeUserData(data.data);
  }
  return data;
}

export async function signinUser(payload, options) {
  return authenticate(API.AUTH.SIGNIN, payload, options);
}

export async function googleAuth(payload, options) {
  // Normal Google login never implicitly confirms a link or creates an account.
  return authenticate(
    API.AUTH.GOOGLE,
    { credential: payload.credential },
    options,
  );
}

export async function linkGoogleAccount(
  credential,
  { expectedUserId, signal },
) {
  if (!expectedUserId) throw new Error("Sign in before connecting Google.");
  return authenticate(
    API.AUTH.GOOGLE,
    { credential, confirm_link: true },
    { expectedUserId, signal },
  );
}

export async function verifyEmailCode(payload) {
  return postWithCsrf(API.AUTH.VERIFY_EMAIL, payload);
}

export async function resendVerificationCode(payload) {
  return postWithCsrf(API.AUTH.RESEND_VERIFICATION_CODE, payload);
}

export async function requestPasswordReset(payload) {
  return postWithCsrf(API.AUTH.REQUEST_PASSWORD_RESET, payload);
}

export async function confirmPasswordReset(payload) {
  return postWithCsrf(API.AUTH.CONFIRM_PASSWORD_RESET, payload);
}

export async function changePassword(payload, accessToken) {
  return postWithCsrf(API.AUTH.CHANGE_PASSWORD, payload, { accessToken });
}

export async function refreshAccessToken() {
  const data = await postWithCsrf(API.AUTH.REFRESH, {});
  storeUserData(data.data);
  return data;
}

export async function getAuthenticatedUser() {
  const data = await getWithCredentials(API.AUTH.ME);
  storeUserData(data.data);
  return data;
}

export async function exportUserData() {
  const response = await fetch(`${API.BASE_URL}${API.AUTH.EXPORT_DATA}`, {
    method: "GET",
    credentials: "include",
    headers: {
      Authorization: `Bearer ${getAccessToken()}`,
    },
  });

  // For blob responses, we cannot use handleFetchResponse (which parses JSON).
  // Handle manually.
  if (!response.ok) {
    const error = new Error("Failed to download data");
    error.status = response.status;
    throw error;
  }
  return response.blob();
}

export async function updateProfile(payload) {
  const csrfToken = await ensureCsrfCookie();
  const headers = {
    "Content-Type": "application/json",
    "X-CSRFToken": csrfToken,
    Authorization: `Bearer ${getAccessToken()}`,
  };

  const response = await fetch(`${API.BASE_URL}${API.AUTH.PROFILE}`, {
    method: "PATCH",
    credentials: "include",
    headers,
    body: JSON.stringify(payload),
  });

  const data = await handleFetchResponse(response);
  // Update stored user data
  if (data?.data?.user) {
    localStorage.setItem(
      STORAGE_KEYS.AUTH_USER,
      JSON.stringify(data.data.user),
    );
  }
  return data;
}

export async function requestEmailChange(payload) {
  const accessToken = getAccessToken();
  return postWithCsrf(API.AUTH.REQUEST_EMAIL_CHANGE, payload, { accessToken });
}

export async function confirmEmailChange(payload) {
  const accessToken = getAccessToken();
  const data = await postWithCsrf(API.AUTH.CONFIRM_EMAIL_CHANGE, payload, {
    accessToken,
  });
  if (data?.data?.user) {
    localStorage.setItem(
      STORAGE_KEYS.AUTH_USER,
      JSON.stringify(data.data.user),
    );
  }
  return data;
}

// Access tokens live 15 minutes. A sign-out or push cleanup sent with an
// expired one is rejected before the refresh cookie can be blacklisted, so a
// verifiably expired departing token is renewed first, while the departing
// account's cookie is still the only one in the browser. Tokens that cannot
// be decoded are treated as unknown and used as they are.
const TOKEN_EXPIRY_SLACK_MS = 30 * 1000;

export function accessTokenExpired(token, now = Date.now()) {
  if (!token) return false;
  try {
    const payload = JSON.parse(
      window.atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
    );
    return (
      typeof payload.exp === "number" &&
      payload.exp * 1000 - TOKEN_EXPIRY_SLACK_MS <= now
    );
  } catch {
    return false;
  }
}

export async function departingAccessToken() {
  const current = getAccessToken();
  if (!accessTokenExpired(current)) return current;
  const generation = getAuthSessionGeneration();
  try {
    const refreshed = await boundedTask(() => renewAccessToken());
    if (generation !== getAuthSessionGeneration()) return null;
    return refreshed?.accessToken || current;
  } catch {
    return current;
  }
}

export async function signoutUser({ authToken = getAccessToken() } = {}) {
  const csrfToken = getCachedCsrfToken();
  // Dispatch the cookie-bearing request synchronously, before local logout
  // or a subsequent login. Never wait for a fresh CSRF request then borrow B's
  // cookie. A missing cached token may fail remotely; local logout still wins.
  const controller = new AbortController();
  const signoutOptions = {
    method: "POST",
    body: {},
    skipAuthRefresh: true,
    skipCsrf: true,
    headers: csrfToken ? { "X-CSRFToken": csrfToken } : {},
    signal: controller.signal,
  };
  const request = (async () => {
    const first = await apiFetch(API.AUTH.SIGNOUT, {
      ...signoutOptions,
      authToken,
    });
    // The refresh cookie alone identifies the session; drop a rejected bearer.
    if (first.status !== 401 || !authToken) return first;
    return apiFetch(API.AUTH.SIGNOUT, {
      ...signoutOptions,
      authToken: null,
      skipAuthHeader: true,
    });
  })();
  const remote = boundedTask(() => request).finally(() => controller.abort());
  const push = unsubscribeFromPush({ authToken });
  clearStoredUserData();
  const [remoteResult, pushResult] = await Promise.allSettled([remote, push]);
  return {
    remoteSignedOut:
      remoteResult.status === "fulfilled" && remoteResult.value.ok,
    push: pushResult.status === "fulfilled" ? pushResult.value : null,
  };
}

export function getStoredUser() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEYS.AUTH_USER));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed
      : null;
  } catch {
    return null;
  }
}

export function getStoredSparkPool() {
  const rawSparkPool = localStorage.getItem(STORAGE_KEYS.SPARK_POOL);
  return rawSparkPool ? JSON.parse(rawSparkPool) : null;
}

export function clearStoredAuth() {
  clearStoredUserData();
}

// -----------------------------------------------------------------
//  Feature preferences (Settings → Advanced)
// -----------------------------------------------------------------

/**
 * Read the per-account optional-feature toggles.
 * @returns {Promise<Object>} - { status, data: { feature_preferences: {...} } }
 */
export async function getFeaturePreferences(options = {}) {
  const response = await apiFetch(API.AUTH.FEATURE_PREFERENCES, {
    method: "GET",
    skipCsrf: true,
    ...options,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data?.data || "Failed to load feature preferences.");
  }
  return response.json();
}

/**
 * Partially update the per-account optional-feature toggles.
 * @param {Object} prefs - e.g. { reminders: true, websearch: false }
 * @returns {Promise<Object>} - { status, data: { feature_preferences: {...} } }
 */
export async function updateFeaturePreferences(prefs, options = {}) {
  const response = await apiFetch(API.AUTH.FEATURE_PREFERENCES, {
    method: "PATCH",
    body: prefs,
    ...options,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(
      data?.data || "Failed to update feature preferences.",
    );
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return response.json();
}
