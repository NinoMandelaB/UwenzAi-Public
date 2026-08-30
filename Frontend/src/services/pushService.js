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
import { apiFetch } from "./apiClient";

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
    body: JSON.stringify({ endpoint, keys }),
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
  const publicKey = await getVapidPublicKey();
  if (!publicKey) {
    throw new Error("Push notifications are not available right now.");
  }

  const registration = await getReadyPushRegistration();
  if (!registration) {
    throw new Error("Push notifications require the installed app.");
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Notification permission was not granted.");
  }

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  });

  try {
    return await registerPushSubscription(subscription);
  } catch (error) {
    // Backend refused — don't leave a half-registered browser subscription
    // that would keep receiving pushes the user can't manage from Settings.
    await subscription.unsubscribe().catch(() => {});
    throw error;
  }
}

export async function unsubscribePushLocally() {
  const subscription = await getPushSubscription();
  if (!subscription) return;
  await subscription.unsubscribe();
}

export async function reconcilePushSubscription() {
  const subscription = await getPushSubscription();
  if (!subscription) return null;
  try {
    return await registerPushSubscription(subscription);
  } catch (error) {
    // Preserve the user's browser-level preference across temporary outages.
    // Only terminal client/conflict responses prove this subscription cannot
    // safely remain associated with the authenticated account.
    if ([400, 409, 410, 422].includes(error?.status)) {
      await subscription.unsubscribe().catch(() => {});
    }
    throw error;
  }
}

/**
 * Remove the subscription backend-side first, then in the browser. Ordering
 * matters on flaky connections: if the browser half fails, the backend row
 * is already gone and the backend prunes dead endpoints on send anyway.
 */
export async function unsubscribeFromPush() {
  const subscription = await getPushSubscription();
  if (!subscription) return;

  let backendError = null;
  let localError = null;
  try {
    const response = await apiFetch(API.PUSH.UNSUBSCRIBE, {
      method: "DELETE",
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
    if (!response.ok) {
      backendError = new Error("Failed to disable push notifications.");
    }
  } catch (error) {
    backendError = error;
  }
  try {
    await subscription.unsubscribe();
  } catch (error) {
    localError = error;
  }
  if (backendError) throw backendError;
  if (localError) throw localError;
}
