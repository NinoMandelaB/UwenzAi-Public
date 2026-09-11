// src/services/pushService.js
//
// Web Push subscription lifecycle. Pairs with Backend capp/classes/push/.
//
// Two deliberate choices:
// - `getRegistration()` over `navigator.serviceWorker.ready`: the app only
//   registers a service worker in production, and `ready` would wait forever
//   in dev. No registration → push is simply unavailable.
// - Push state lives in the browser (the PushManager subscription), not in
//   localStorage: the browser is the source of truth and re-syncs itself.

import { API } from "../config/constants";
import {
  apiFetch,
  getAccessToken,
  getAuthSessionGeneration,
} from "./apiClient";
import { boundedTask } from "../utils/boundedTask";

export const PUSH_STATE_CHANGED = "uwenzai:push-state-changed";
const notifyPushState = () =>
  window.dispatchEvent(new Event(PUSH_STATE_CHANGED));

/**
 * Whether this browser can receive Web Push at all
 * (service worker + PushManager + Notification APIs).
 */
export function isPushSupported() {
  return (
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/**
 * Convert the URL-safe base64 VAPID public key into the Uint8Array
 * applicationServerKey that PushManager.subscribe expects.
 */
export function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

/**
 * Fetch the VAPID public key. Returns null when push is not configured on
 * the backend (503) — callers treat that as "feature unavailable", not an
 * error worth surfacing.
 */
export async function getVapidPublicKey() {
  const response = await apiFetch(API.PUSH.VAPID_PUBLIC_KEY, {
    method: "GET",
    skipAuthHeader: true,
    skipCsrf: true,
  });
  if (response.status === 503) {
    return null;
  }
  if (!response.ok) {
    throw new Error("Failed to load push configuration.");
  }
  const data = await response.json();
  return data?.data?.public_key ?? null;
}

/**
 * The browser's current push subscription, or null.
 */
export async function getPushSubscription() {
  if (!isPushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration();
  if (!registration) return null;
  return registration.pushManager.getSubscription();
}

async function getReadyPushRegistration() {
  if (!isPushSupported()) return null;
  const existing = await navigator.serviceWorker.getRegistration();
  if (!existing) return null;
  return navigator.serviceWorker.ready;
}

async function registerPushSubscription(subscription) {
  const { endpoint, keys } = subscription.toJSON();
  const response = await apiFetch(API.PUSH.SUBSCRIBE, {
    method: "POST",
    body: { endpoint, keys },
  });
  if (!response.ok) {
    const error = new Error("Failed to enable push notifications.");
    error.status = response.status;
    throw error;
  }
  return subscription;
}

/**
 * Ask for notification permission, subscribe the browser and register the
 * subscription with the backend. Returns the PushSubscription.
 * Throws when permission is denied or the backend rejects the subscription.
 */
export async function subscribeToPush() {
  const generation = getAuthSessionGeneration();
  const publicKey = await getVapidPublicKey();
  if (!publicKey) {
    throw new Error("Push notifications are not available right now.");
  }

  const registration = await getReadyPushRegistration();
  if (getAuthSessionGeneration() !== generation)
    throw new Error("Session changed.");
  if (!registration) {
    throw new Error("Push notifications require the installed app.");
  }

  const permission = await Notification.requestPermission();
  if (getAuthSessionGeneration() !== generation)
    throw new Error("Session changed.");
  if (permission !== "granted") {
    throw new Error("Notification permission was not granted.");
  }

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  });

  try {
    if (getAuthSessionGeneration() !== generation)
      throw new Error("Session changed.");
    const registered = await registerPushSubscription(subscription);
    if (getAuthSessionGeneration() !== generation)
      throw new Error("Session changed.");
    notifyPushState();
    return registered;
  } catch (error) {
    // Backend refused — don't leave a half-registered browser subscription
    // that would keep receiving pushes the user can't manage from Settings.
    // A delayed refusal from A must not retire a subscription B now owns.
    if (getAuthSessionGeneration() === generation || !getAccessToken()) {
      await boundedTask(() => subscription.unsubscribe()).catch(() => {});
      notifyPushState();
    }
    throw error;
  }
}

export async function unsubscribePushLocally() {
  const token = getAccessToken();
  try {
    return await boundedTask(async () => {
      const subscription = await getPushSubscription();
      if (getAccessToken() && getAccessToken() !== token) return false;
      if (!subscription) return true;
      return subscription.unsubscribe();
    });
  } finally {
    notifyPushState();
  }
}

export async function reconcilePushSubscription() {
  const generation = getAuthSessionGeneration();
  const subscription = await getPushSubscription();
  if (getAuthSessionGeneration() !== generation) return null;
  if (!subscription) return null;
  try {
    return await registerPushSubscription(subscription);
  } catch (error) {
    // Preserve the user's browser-level preference across temporary outages.
    // Only terminal client/conflict responses prove this subscription cannot
    // safely remain associated with the authenticated account.
    if (
      getAuthSessionGeneration() === generation &&
      [400, 409, 410, 422].includes(error?.status)
    ) {
      await boundedTask(() => subscription.unsubscribe()).catch(() => {});
      notifyPushState();
    }
    throw error;
  }
}

/**
 * Retire local and server state independently. Neither side may hold the
 * other hostage. Explicit outcomes let Settings report partial success.
 */
export async function unsubscribeFromPush({
  authToken = getAccessToken(),
} = {}) {
  let subscription;
  try {
    subscription = await boundedTask(() => getPushSubscription());
    if (getAccessToken() && getAccessToken() !== authToken) {
      return {
        localRemoved: false,
        remoteRemoved: false,
        localError: new Error("Session changed."),
      };
    }
  } catch (localError) {
    return { localRemoved: false, remoteRemoved: false, localError };
  }
  if (!subscription) return { localRemoved: true, remoteRemoved: true };
  const [local, remote] = await Promise.allSettled([
    boundedTask(async () => {
      const removed = await subscription.unsubscribe();
      if (!removed && (await getPushSubscription()))
        throw new Error("Browser subscription remains enabled.");
      return true;
    }).finally(notifyPushState),
    boundedTask(async (signal) => {
      const response = await apiFetch(API.PUSH.UNSUBSCRIBE, {
        method: "DELETE",
        body: { endpoint: subscription.endpoint },
        authToken,
        skipAuthRefresh: true,
        signal,
      });
      if (!response.ok) throw new Error("Server push cleanup failed.");
      return true;
    }),
  ]);
  return {
    localRemoved: local.status === "fulfilled",
    remoteRemoved: remote.status === "fulfilled",
    localError: local.reason || null,
    remoteError: remote.reason || null,
  };
}
