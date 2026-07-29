// src/services/billingService.js
import { API } from "../config/constants";
import { apiFetch } from "./apiClient";

/* ------------------------------------------------------------------ */
/*  Shared error‑handling helpers (same as feedbackService)            */
/* ------------------------------------------------------------------ */
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

/* ------------------------------------------------------------------ */
/*  Public endpoints                                                   */
/* ------------------------------------------------------------------ */

/**
 * Get available spark packages and subscription plans.
 */
export async function listPackages() {
  const response = await apiFetch(API.PACKAGES, { method: "GET" });
  return handleResponse(response);
}

/**
 * Initiate a payment using a predefined package slug.
 *
 * Only what to buy is sent. Price, currency and the post-payment return URL are
 * all decided server-side, so nothing here can influence what is charged or
 * where the customer comes back to.
 */
export async function initiatePayment({
  package_slug,
  package_type,
  isGuest = false,
}) {
  const response = await apiFetch(API.PAYSTACK.INITIATE, {
    method: "POST",
    body: { package_slug, package_type },
    // A guest must not send a stale Authorization header, or the backend would
    // treat the call as authenticated and never look at the guest cookie.
    ...(isGuest ? { skipAuthHeader: true } : {}),
  });
  return handleResponse(response);
}

/**
 * Verify a guest's payment on return from Paystack.
 *
 * The reference alone is not proof of ownership — it travels in a redirect URL
 * that anyone can see — so the backend matches it against the guest credential
 * cookie instead.
 */
export async function verifyGuestPayment(reference) {
  const params = new URLSearchParams({ reference });
  const response = await apiFetch(
    `${API.PAYSTACK.VERIFY}?${params.toString()}`,
    { method: "GET", skipAuthHeader: true },
  );
  return handleResponse(response);
}

/* ------------------------------------------------------------------ */
/*  Admin: Spark Packages                                               */
/* ------------------------------------------------------------------ */
export async function listSparkPackages() {
  const response = await apiFetch(API.ADMIN_PACKAGES.SPARK_PACKAGES.LIST, {
    method: "GET",
  });
  return handleResponse(response);
}

export async function createSparkPackage(payload) {
  const response = await apiFetch(API.ADMIN_PACKAGES.SPARK_PACKAGES.CREATE, {
    method: "POST",
    body: payload,
  });
  return handleResponse(response);
}

export async function updateSparkPackage(slug, payload) {
  const response = await apiFetch(
    API.ADMIN_PACKAGES.SPARK_PACKAGES.UPDATE(slug),
    {
      method: "PUT",
      body: payload,
    },
  );
  return handleResponse(response);
}

export async function deleteSparkPackage(slug) {
  const response = await apiFetch(
    API.ADMIN_PACKAGES.SPARK_PACKAGES.DELETE(slug),
    {
      method: "DELETE",
    },
  );
  return handleResponse(response);
}

/* ------------------------------------------------------------------ */
/*  Admin: Subscription Plans                                          */
/* ------------------------------------------------------------------ */
export async function listSubscriptionPlans() {
  const response = await apiFetch(API.ADMIN_PACKAGES.SUBSCRIPTION_PLANS.LIST, {
    method: "GET",
  });
  return handleResponse(response);
}

export async function createSubscriptionPlan(payload) {
  const response = await apiFetch(
    API.ADMIN_PACKAGES.SUBSCRIPTION_PLANS.CREATE,
    {
      method: "POST",
      body: payload,
    },
  );
  return handleResponse(response);
}

/**
 * Get the authenticated user's spark balance.
 */
export async function getSparkPoolBalance() {
  const response = await apiFetch(API.SPARK_POOL.BALANCE, { method: "GET" });
  return handleResponse(response);
}

/**
 * Get recent spark_pool transactions (last 10).
 */
export async function getSparkTransactions() {
  const response = await apiFetch(API.SPARK_POOL.TRANSACTIONS, {
    method: "GET",
  });
  return handleResponse(response);
}

export async function getCurrentSubscription() {
  const response = await apiFetch(API.SUBSCRIPTION, { method: "GET" });
  return handleResponse(response);
}

export async function updateSubscriptionPlan(slug, payload) {
  const response = await apiFetch(
    API.ADMIN_PACKAGES.SUBSCRIPTION_PLANS.UPDATE(slug),
    {
      method: "PUT",
      body: payload,
    },
  );
  return handleResponse(response);
}

export async function verifyPayment(reference) {
  const response = await apiFetch(
    `${API.PAYSTACK.VERIFY}?reference=${encodeURIComponent(reference)}`,
    { method: "GET" },
  );
  return handleResponse(response);
}

export async function getMessagesTodayCount() {
  const response = await apiFetch(API.MESSAGES.TODAY_COUNT, { method: "GET" });
  return handleResponse(response);
}

export async function getSparkTransactionsPaginated({
  page = 1,
  pageSize = 20,
  search = "",
  startDate = "",
  endDate = "",
} = {}) {
  const params = new URLSearchParams();
  params.set("page", page);
  params.set("page_size", pageSize);
  if (search) params.set("search", search);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);

  const response = await apiFetch(
    `${API.SPARK_POOL.TRANSACTIONS_PAGINATED}?${params.toString()}`,
    { method: "GET" },
  );
  return handleResponse(response);
}

/**
 * Get current pricing constants (spark costs, limits, etc.).
 */
export async function getPricing() {
  const response = await apiFetch(API.PRICING.GET, { method: "GET" });
  return handleResponse(response);
}

export async function deleteSubscriptionPlan(slug) {
  const response = await apiFetch(
    API.ADMIN_PACKAGES.SUBSCRIPTION_PLANS.DELETE(slug),
    {
      method: "DELETE",
    },
  );
  return handleResponse(response);
}
