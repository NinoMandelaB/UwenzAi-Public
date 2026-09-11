import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePushNotifications } from "./usePushNotifications";
import { PUSH_STATE_CHANGED } from "../services/pushService";
import { apiFetch } from "../services/apiClient";
import { CLEANUP_TIMEOUT_MS } from "../utils/boundedTask";
vi.mock("../services/apiClient", () => ({
  apiFetch: vi.fn(),
  getAccessToken: () => "A-token",
}));
let subscription;
let unsubscribe;
beforeEach(() => {
  vi.clearAllMocks();
  unsubscribe = vi.fn().mockImplementation(async () => {
    subscription = null;
    return true;
  });
  subscription = { endpoint: "https://push.example/A", unsubscribe };
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {
      getRegistration: vi.fn().mockResolvedValue({
        pushManager: { getSubscription: async () => subscription },
      }),
    },
  });
  Object.defineProperty(window, "PushManager", {
    configurable: true,
    value: class {},
  });
  Object.defineProperty(window, "Notification", {
    configurable: true,
    value: { permission: "granted" },
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("shows OFF and a partial-cleanup warning when only server removal fails", async () => {
  apiFetch.mockResolvedValue({ ok: false, status: 500 });
  const { result } = renderHook(usePushNotifications);
  await waitFor(() => expect(result.current.enabled).toBe(true));
  await act(async () => result.current.setPushEnabled(false));
  expect(result.current.enabled).toBe(false);
  expect(result.current.error).toBe("pushRemoteCleanupWarning");
  expect(result.current.cleanupOutcome).toMatchObject({
    localRemoved: true,
    remoteRemoved: false,
  });
});
it("keeps ON and an error when local removal fails independently of the server", async () => {
  apiFetch.mockResolvedValue({ ok: true });
  unsubscribe.mockRejectedValue(new Error("browser failed"));
  const { result } = renderHook(usePushNotifications);
  await waitFor(() => expect(result.current.enabled).toBe(true));
  await act(async () => result.current.setPushEnabled(false));
  expect(result.current.enabled).toBe(true);
  expect(result.current.error).toBe("pushLocalCleanupError");
  expect(result.current.cleanupOutcome.remoteRemoved).toBe(true);
});
it("bounds a hung server request without holding local unsubscribe", async () => {
  apiFetch.mockReturnValue(new Promise(() => {}));
  const { result } = renderHook(usePushNotifications);
  await waitFor(() => expect(result.current.enabled).toBe(true));
  vi.useFakeTimers();
  let pending;
  await act(async () => {
    pending = result.current.setPushEnabled(false);
  });
  expect(unsubscribe).toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(CLEANUP_TIMEOUT_MS + 1);
    await pending;
  });
  expect(result.current.enabled).toBe(false);
  expect(apiFetch.mock.calls[0][1].signal.aborted).toBe(true);
});
it("refreshes local state after background reconciliation", async () => {
  const { result } = renderHook(usePushNotifications);
  await waitFor(() => expect(result.current.enabled).toBe(true));
  subscription = null;
  await act(async () => window.dispatchEvent(new Event(PUSH_STATE_CHANGED)));
  expect(result.current.enabled).toBe(false);
});
