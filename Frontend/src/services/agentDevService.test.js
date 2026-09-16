import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./apiClient";
import { LEGAL_POLICY_VERSION } from "../i18n/legal";
import {
  agentChatCompletion,
  confirmConsent,
  createAgentKey,
  deletePlaygroundHistory,
  getAgentPricing,
  getBaseURL,
  getConsent,
  getPlaygroundHistory,
  listAgentKeys,
  listAgentModels,
  playgroundCompletion,
  revokeAgentKey,
} from "./agentDevService";
vi.mock("./apiClient", () => ({ apiFetch: vi.fn() }));
const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: vi.fn().mockResolvedValue(body),
});
const ok = { status: "ok", data: { keys: [] } };
beforeEach(() => {
  vi.resetAllMocks();
  apiFetch.mockResolvedValue(response(ok));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("canonical developer API service", () => {
  it("preserves the gateway's top-level consent version mismatch response", async () => {
    apiFetch.mockResolvedValueOnce(
      response(
        {
          status: "consent_version_mismatch",
          message: "Review the current developer notice.",
          version: "2099-01-01",
        },
        409,
      ),
    );
    await expect(confirmConsent()).rejects.toMatchObject({
      code: "consent_version_mismatch",
      status: 409,
    });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch.mock.calls[0][1].body.version).toBe(LEGAL_POLICY_VERSION);
  });
  it("does not treat an ok status on a failed HTTP response as an error code", async () => {
    apiFetch.mockResolvedValueOnce(response({ status: "ok" }, 500));
    await expect(getConsent()).rejects.toMatchObject({
      code: "request_failed",
    });
  });
  it("preserves explicit terminal/refund confirmation, never inferring it from HTTP 502", async () => {
    apiFetch.mockResolvedValueOnce(
      response(
        {
          error: {
            status: "upstream_error",
            terminal: true,
            refund_confirmed: true,
          },
        },
        502,
      ),
    );
    await expect(playgroundCompletion({})).rejects.toMatchObject({
      code: "upstream_error",
      terminal: true,
      refund_confirmed: true,
    });
    apiFetch.mockResolvedValueOnce(
      response({ error: { status: "upstream_error" } }, 502),
    );
    await expect(playgroundCompletion({})).rejects.toMatchObject({
      terminal: false,
      refund_confirmed: false,
    });
  });
  it("uses canonical session auth, passes CSRF-capable object bodies, and preserves key CRUD shapes", async () => {
    await listAgentKeys();
    expect(apiFetch).toHaveBeenLastCalledWith(
      "/api/v1/agent/keys/",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    await createAgentKey({ label: "My app" });
    expect(apiFetch).toHaveBeenLastCalledWith(
      "/api/v1/agent/keys/",
      expect.objectContaining({ method: "POST", body: { label: "My app" } }),
    );
    await revokeAgentKey("key/id");
    expect(apiFetch).toHaveBeenLastCalledWith(
      "/api/v1/agent/keys/key%2Fid/",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(
      apiFetch.mock.calls.every(
        ([, options]) =>
          !options.headers?.Authorization && options.authToken === undefined,
      ),
    ).toBe(true);
  });
  it("loads authoritative pricing baseURL instead of deriving an obsolete endpoint", async () => {
    apiFetch.mockResolvedValue(
      response({
        status: "ok",
        data: { base_url: "https://authoritative.example.test/gateway" },
      }),
    );
    expect(await getBaseURL()).toEqual({
      base_url: "https://authoritative.example.test/gateway",
    });
    expect(apiFetch.mock.calls[0][0]).toBe("/api/v1/agent/pricing/");
  });
  it("rejects a missing authoritative URL", async () => {
    await expect(getBaseURL()).rejects.toMatchObject({
      code: "invalid_response",
    });
  });
  it("sends consent checks and confirmations through the same session client", async () => {
    await getConsent();
    await confirmConsent();
    expect(apiFetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/agent/consent/",
      "/api/v1/agent/consent/",
    ]);
    expect(apiFetch.mock.calls[1][1].method).toBe("POST");
    expect(apiFetch.mock.calls[1][1].body).toEqual({
      accepted: true,
      version: LEGAL_POLICY_VERSION,
    });
    expect(apiFetch.mock.calls[0][1].body).toBeUndefined();
  });
  it("cannot replace the displayed consent version with a caller-supplied body", async () => {
    await confirmConsent({
      body: { accepted: true, version: "2099-01-01" },
    });
    expect(apiFetch.mock.calls[0][1].body).toEqual({
      accepted: true,
      version: LEGAL_POLICY_VERSION,
    });
  });
  it("preserves a billed playground's exact payload and returns OpenAI response", async () => {
    const result = {
      choices: [{ message: { content: "Reply" } }],
      usage: { prompt_tokens: 12 },
      _uwenzai: { sparks_charged: 1 },
    };
    apiFetch.mockResolvedValue(response(result));
    const body = {
      key_id: "id",
      provider: "any-new-provider",
      model: "new-model",
      messages: [{ role: "user", content: "Hello" }],
      max_tokens: 32,
      save_history: false,
      client_operation_id: "546f6b9b-a1d5-4cc6-8f24-e453e03212d1",
    };
    expect(await playgroundCompletion(body)).toEqual(result);
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/agent/playground/",
      expect.objectContaining({ method: "POST", body }),
    );
  });
  it("encodes provider/model routing in GET and DELETE history requests", async () => {
    const pair = { model: "org/model x", provider: "provider+a" };
    await getPlaygroundHistory(pair);
    await deletePlaygroundHistory(pair);
    expect(apiFetch.mock.calls[0][0]).toBe(
      "/api/v1/agent/playground/history/?model=org%2Fmodel+x&provider=provider%2Ba",
    );
    expect(apiFetch.mock.calls[1][1].method).toBe("DELETE");
  });
  it.each([400, 401, 402, 403, 429, 500])(
    "rejects non-OK HTTP %i with backend status metadata",
    async (status) => {
      apiFetch.mockResolvedValue(
        response(
          {
            status: "error",
            error: { status: "upstream_failed", message: "Visible failure" },
          },
          status,
        ),
      );
      await expect(listAgentKeys()).rejects.toMatchObject({
        status,
        code: "upstream_failed",
        message: "Visible failure",
      });
    },
  );
  it("rejects application error envelopes even when HTTP is 200", async () => {
    apiFetch.mockResolvedValue(
      response({ status: "error", data: { message: "Cannot create" } }),
    );
    await expect(createAgentKey({ label: "App" })).rejects.toThrow(
      "Cannot create",
    );
  });
  it("handles non-JSON gateway errors without masking HTTP status", async () => {
    apiFetch.mockResolvedValue({
      ok: false,
      status: 502,
      json: vi.fn().mockRejectedValue(new SyntaxError("HTML")),
    });
    await expect(getAgentPricing()).rejects.toMatchObject({
      status: 502,
      code: "invalid_response",
    });
  });
  it("settles on a caller abort even when apiFetch ignores the signal", async () => {
    apiFetch.mockImplementation(() => new Promise(() => {}));
    const controller = new AbortController();
    const promise = listAgentKeys({ signal: controller.signal });
    const assertion = expect(promise).rejects.toMatchObject({
      name: "AbortError",
    });
    controller.abort();
    await assertion;
    expect(apiFetch.mock.calls[0][1].signal.aborted).toBe(true);
  });
  it("does not start a request when the caller is already aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      listAgentKeys({ signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(apiFetch).not.toHaveBeenCalled();
  });
  it("bounds pending CSRF/network/JSON with a configurable timeout", async () => {
    vi.useFakeTimers();
    apiFetch.mockImplementation(() => new Promise(() => {}));
    const promise = playgroundCompletion({}, { timeoutMs: 123 });
    const assertion = expect(promise).rejects.toMatchObject({
      code: "timeout",
    });
    await vi.advanceTimersByTimeAsync(123);
    await assertion;
    expect(apiFetch.mock.calls[0][1].signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("times out a JSON body that never settles", async () => {
    vi.useFakeTimers();
    apiFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: () => new Promise(() => {}),
    });
    const promise = listAgentKeys({ timeoutMs: 50 });
    const assertion = expect(promise).rejects.toMatchObject({
      code: "timeout",
    });
    await vi.advanceTimersByTimeAsync(50);
    await assertion;
  });
  it("clears deadline timers after successful requests", async () => {
    vi.useFakeTimers();
    await listAgentKeys({ timeoutMs: 100 });
    expect(vi.getTimerCount()).toBe(0);
  });
  it("preserves external-agent helper exports without using them in the workspace", async () => {
    const externalFetch = vi
      .fn()
      .mockResolvedValue(response({ choices: [], data: [] }));
    vi.stubGlobal("fetch", externalFetch);
    await listAgentModels("test-only-external-key");
    await agentChatCompletion("test-only-external-key", { model: "alpha" });
    expect(externalFetch.mock.calls[0][0]).toContain("/models/");
    expect(externalFetch.mock.calls[1][0]).toContain("/chat/completions/");
    expect(externalFetch.mock.calls[1][1].body).toBe('{"model":"alpha"}');
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
