import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./apiClient", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "./apiClient";
import {
  reconcilePushSubscription,
  subscribeToPush,
  unsubscribeFromPush,
  urlBase64ToUint8Array,
} from "./pushService";

// VAPID public keys are URL-safe base64 without padding; PushManager wants
// the raw bytes. A wrong conversion subscribes against a different key and
// every send fails with an auth error, so this gets pinned down exactly.
describe("urlBase64ToUint8Array", () => {
  it("decodes a real VAPID public key to its 65 bytes", () => {
    // Well-known uncompressed P-256 point: always starts with 0x04.
    const key =
      "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U";

    const bytes = urlBase64ToUint8Array(key);

    expect(bytes).toBeInstanceOf(Uint8Array);
    expect(bytes.length).toBe(65);
    expect(bytes[0]).toBe(4);
  });

  it("handles url-safe characters and missing padding", () => {
    // Bytes that encode to + and / in standard base64 (=> - and _ url-safe),
    // with a length that needs padding stripped.
    const original = new Uint8Array([251, 255, 190, 239, 250]);
    const base64Url = window
      .btoa(String.fromCharCode(...original))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");

    const decoded = urlBase64ToUint8Array(base64Url);

    expect(Array.from(decoded)).toEqual(Array.from(original));
  });
});

describe("push subscription lifecycle", () => {
  let subscription;
  let registration;

  beforeEach(() => {
    vi.clearAllMocks();
    subscription = {
      endpoint: "https://fcm.googleapis.com/push/1",
      toJSON: vi.fn(() => ({
        endpoint: "https://fcm.googleapis.com/push/1",
        keys: { p256dh: "key", auth: "secret" },
      })),
      unsubscribe: vi.fn().mockResolvedValue(true),
    };
    registration = {
      pushManager: {
        getSubscription: vi.fn().mockResolvedValue(subscription),
        subscribe: vi.fn().mockResolvedValue(subscription),
      },
    };
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        getRegistration: vi.fn().mockResolvedValue(registration),
        ready: Promise.resolve(registration),
      },
    });
    Object.defineProperty(window, "PushManager", {
      configurable: true,
      value: class PushManager {},
    });
    Object.defineProperty(window, "Notification", {
      configurable: true,
      value: { requestPermission: vi.fn().mockResolvedValue("granted") },
    });
  });

  it("checks service-worker readiness before requesting permission", async () => {
    apiFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ data: { public_key: "BA" } }),
    });
    navigator.serviceWorker.getRegistration.mockResolvedValueOnce(null);

    await expect(subscribeToPush()).rejects.toThrow(
      "Push notifications require the installed app.",
    );
    expect(Notification.requestPermission).not.toHaveBeenCalled();
  });

  it("removes the browser subscription when backend registration fails", async () => {
    apiFetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ data: { public_key: "BA" } }),
      })
      .mockResolvedValueOnce({ ok: false });

    await expect(subscribeToPush()).rejects.toThrow(
      "Failed to enable push notifications.",
    );
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();
  });

  it("still unsubscribes locally when backend cleanup fails", async () => {
    apiFetch.mockRejectedValueOnce(new Error("offline"));

    await expect(unsubscribeFromPush()).rejects.toThrow("offline");
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();
  });

  it("reconciles an existing subscription with the authenticated account", async () => {
    apiFetch.mockResolvedValueOnce({ ok: true });

    await expect(reconcilePushSubscription()).resolves.toBe(subscription);
    expect(apiFetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(subscription.toJSON()),
      }),
    );
  });

  it("preserves the local subscription during a transient backend outage", async () => {
    apiFetch.mockResolvedValueOnce({ ok: false, status: 503 });

    await expect(reconcilePushSubscription()).rejects.toThrow(
      "Failed to enable push notifications.",
    );
    expect(subscription.unsubscribe).not.toHaveBeenCalled();
  });

  it("fails closed locally when account ownership conflicts", async () => {
    apiFetch.mockResolvedValueOnce({ ok: false, status: 409 });

    await expect(reconcilePushSubscription()).rejects.toThrow(
      "Failed to enable push notifications.",
    );
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();
  });
});
