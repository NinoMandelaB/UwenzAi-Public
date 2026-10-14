// Agent Gateway requests use the canonical session client, including CSRF and refresh.
import { apiFetch } from "./apiClient";
import { LEGAL_POLICY_VERSION } from "../i18n/legal";

const AGENT_BASE_PATH = "/api/v1/agent";
export const AGENT_REQUEST_TIMEOUT_MS = 30000;
export const PLAYGROUND_TIMEOUT_MS = 120000;

export class AgentRequestError extends Error {
  constructor(message, status = 0, code = "request_failed", metadata = {}) {
    super(message);
    this.name = "AgentRequestError";
    this.status = status;
    this.code = code;
    this.terminal = metadata.terminal === true;
    this.refund_confirmed = metadata.refund_confirmed === true;
  }
}

// Race the entire operation (including CSRF/refresh/JSON) against a deadline.
// Some network implementations do not settle immediately when aborted.
async function request(
  path,
  { signal, timeoutMs = AGENT_REQUEST_TIMEOUT_MS, ...options } = {},
  externalKey,
) {
  const controller = new AbortController();
  let timer;
  let abortListener;
  const deadline = new Promise((_, reject) => {
    abortListener = () => {
      controller.abort();
      reject(new DOMException("Request cancelled.", "AbortError"));
    };
    if (signal?.aborted) abortListener();
    else signal?.addEventListener("abort", abortListener, { once: true });
    timer = setTimeout(() => {
      controller.abort();
      reject(new AgentRequestError("Request timed out.", 0, "timeout"));
    }, timeoutMs);
  });
  const work = async () => {
    if (controller.signal.aborted)
      throw new DOMException("Request cancelled.", "AbortError");
    const response =
      externalKey === undefined
        ? await apiFetch(`${AGENT_BASE_PATH}${path}`, {
            ...options,
            signal: controller.signal,
          })
        : await fetch(
            `${(import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "")}${AGENT_BASE_PATH}${path}`,
            {
              ...options,
              signal: controller.signal,
              headers: {
                Authorization: `Bearer ${externalKey}`,
                "Content-Type": "application/json",
              },
              body: options.body ? JSON.stringify(options.body) : undefined,
            },
          );
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new AgentRequestError(
        "Invalid server response.",
        response.status,
        "invalid_response",
      );
    }
    if (
      !response.ok ||
      payload?.status === "error" ||
      payload?.error ||
      (payload?.status && payload.status !== "ok")
    ) {
      throw new AgentRequestError(
        payload?.error?.message ||
          payload?.data?.message ||
          payload?.message ||
          "Request failed.",
        response.status,
        payload?.error?.code ||
          payload?.error?.status ||
          (typeof payload?.status === "string" &&
          !["ok", "error"].includes(payload.status)
            ? payload.status
            : "request_failed"),
        payload?.error || {},
      );
    }
    if (!payload || typeof payload !== "object")
      throw new AgentRequestError(
        "Invalid server response.",
        response.status,
        "invalid_response",
      );
    return payload;
  };
  try {
    return await Promise.race([work(), deadline]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abortListener);
  }
}

export const listAgentKeys = (options) => request("/keys/", options);
export const createAgentKey = (params = {}, options) =>
  request("/keys/", { ...options, method: "POST", body: params });
export const revokeAgentKey = (keyId, options) =>
  request(`/keys/${encodeURIComponent(keyId)}/`, {
    ...options,
    method: "DELETE",
  });
export const updateAgentKeyBudget = (keyId, budget, options) =>
  request(`/keys/${encodeURIComponent(keyId)}/budget/`, {
    ...options,
    method: "PATCH",
    body: { monthly_spark_budget: budget },
  });
export const getAgentUsage = (options) => request("/usage/", options);
export const getConsent = (options) => request("/consent/", options);
export const confirmConsent = (options) =>
  request("/consent/", {
    ...options,
    method: "POST",
    body: { accepted: true, version: LEGAL_POLICY_VERSION },
  });
export const getAgentPricing = (options) => request("/pricing/", options);
// Preserve the helper's return shape, but never invent the public gateway URL.
export const getBaseURL = async (options) => {
  const result = await getAgentPricing(options);
  if (!result.data?.base_url)
    throw new AgentRequestError("Missing gateway URL.", 0, "invalid_response");
  return { base_url: result.data.base_url };
};
export const playgroundCompletion = (body, options) =>
  request("/playground/", {
    timeoutMs: PLAYGROUND_TIMEOUT_MS,
    ...options,
    method: "POST",
    body,
  });
const historyPath = ({ model, provider }) =>
  `/playground/history/?${new URLSearchParams({ model, provider })}`;
export const getPlaygroundHistory = (pair, options) =>
  request(historyPath(pair), options);
export const deletePlaygroundHistory = (pair, options) =>
  request(historyPath(pair), { ...options, method: "DELETE" });
// External-agent helpers remain available; the workspace itself never needs a raw key.
export const listAgentModels = (apiKey, options) =>
  request("/models/", options, apiKey);
export const agentChatCompletion = (apiKey, body, options) =>
  request(
    "/chat/completions/",
    { timeoutMs: PLAYGROUND_TIMEOUT_MS, ...options, method: "POST", body },
    apiKey,
  );

export default {
  getBaseURL,
  listAgentKeys,
  createAgentKey,
  revokeAgentKey,
  getAgentUsage,
  listAgentModels,
  agentChatCompletion,
  getConsent,
  confirmConsent,
  getAgentPricing,
  playgroundCompletion,
  getPlaygroundHistory,
  deletePlaygroundHistory,
};
