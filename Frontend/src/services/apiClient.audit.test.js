import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { API } from "../config/constants";
import {
  apiFetch,
  clearAccessToken,
  clearCsrfCache,
  getAccessToken,
  invalidateAuthRequests,
  refreshAccessToken,
  setAccessToken,
} from "./apiClient";
const response = (status, data) => ({
  status,
  ok: status >= 200 && status < 300,
  json: async () => data,
});
beforeEach(() => {
  invalidateAuthRequests();
  clearAccessToken();
  clearCsrfCache();
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => vi.unstubAllGlobals());
const csrf = response(200, { data: { csrfToken: "csrf-A" } });
it.each([401, 403])(
  "classifies confirmed refresh rejection %s as session loss",
  async (status) => {
    fetch
      .mockResolvedValueOnce(csrf)
      .mockResolvedValueOnce(response(status, { status: "fail" }));
    expect(await refreshAccessToken()).toBe(null);
  },
);
it.each([500, 503])(
  "does not classify transient refresh response %s as confirmed expiry",
  async (status) => {
    fetch
      .mockResolvedValueOnce(csrf)
      .mockResolvedValueOnce(response(status, { status: "fail" }));
    await expect(refreshAccessToken()).rejects.toThrow(
      "temporarily unavailable",
    );
  },
);
it("does not resurrect an old token when logout/login wins a pending refresh", async () => {
  let resolve;
  fetch.mockResolvedValueOnce(csrf).mockReturnValueOnce(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const pending = refreshAccessToken();
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  invalidateAuthRequests();
  setAccessToken("B-token");
  resolve(
    response(200, { status: "success", data: { access_token: "old-A-token" } }),
  );
  expect(await pending).toBe(null);
  expect(getAccessToken()).toBe("B-token");
});
it("never borrows B's token when A's PATCH is delayed by CSRF initialization", async () => {
  let resolve;
  setAccessToken("A-token");
  fetch.mockReturnValueOnce(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const pending = apiFetch(API.AUTH.FEATURE_PREFERENCES, {
    method: "PATCH",
    body: { reminders: true },
  });
  const rejection = expect(pending).rejects.toMatchObject({
    name: "AbortError",
  });
  invalidateAuthRequests();
  setAccessToken("B-token");
  resolve(csrf);
  await rejection;
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("honors an explicitly pinned cleanup token and abort signal", async () => {
  setAccessToken("B-token");
  fetch.mockResolvedValue(response(200, {}));
  await apiFetch(API.PUSH.UNSUBSCRIBE, {
    method: "DELETE",
    body: { endpoint: "A" },
    authToken: "A-token",
    skipCsrf: true,
    skipAuthRefresh: true,
  });
  expect(fetch.mock.calls[0][1].headers.Authorization).toBe("Bearer A-token");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ endpoint: "A" });
});
