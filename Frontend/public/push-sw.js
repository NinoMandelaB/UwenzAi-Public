/*
 * Push event handlers for the UwenzAi service worker.
 *
 * This file is merged into the generated service worker at build time
 * (vite.config.js → workbox.importScripts). It must stay dependency-free
 * plain service-worker code — no imports, no app code.
 *
 * Payloads are metadata-only by design (see Backend push_service.py): a
 * title, a generic body, a conversation URL and the owner's total unread
 * count. Never message content.
 */

self.addEventListener("push", (event) => {
  if (!event.data) {
    return;
  }

  let payload;
  try {
    payload = event.data.json();
  } catch {
    return;
  }

  const title = payload.title || "UwenzAi";
  // Present on new-message pushes; mirrors the in-app nav badge onto the
  // launcher icon (Badging API, where supported).
  const unreadCount = Number.isInteger(payload.unread_count)
    ? payload.unread_count
    : null;
  const options = {
    body: payload.body || "",
    icon: "/pwa-192.png",
    badge: "/pwa-192.png",
    // Same-conversation pushes replace each other instead of stacking one
    // notification per message.
    tag: payload.url || "uwenzai",
    renotify: false,
    data: { url: payload.url || "/" },
  };

  // While an app window is visible, the WebSocket has already updated the
  // UI — a system notification on top would be noise. Each device decides
  // for itself, so a closed phone still notifies while the desktop app is
  // open, and vice versa.
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((windowClients) => {
        // The badge matters in both branches: with no visible window the icon
        // is the only place the count shows, and with a visible one this just
        // races the foreground's own WebSocket sync to the same value.
        let badgeUpdate = Promise.resolve();
        if (unreadCount !== null) {
          badgeUpdate = Promise.resolve()
            .then(() => {
              if (unreadCount > 0 && "setAppBadge" in self.navigator) {
                return self.navigator.setAppBadge(unreadCount);
              }
              if (unreadCount === 0 && "clearAppBadge" in self.navigator) {
                return self.navigator.clearAppBadge();
              }
              return undefined;
            })
            .catch(() => {});
        }
        const appVisible = windowClients.some(
          (client) =>
            new URL(client.url).origin === self.location.origin &&
            client.visibilityState === "visible",
        );
        if (appVisible) {
          return badgeUpdate;
        }
        return badgeUpdate.then(() =>
          self.registration.showNotification(title, options),
        );
      }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || "/";

  // Prefer focusing an already-open app window over spawning a new one.
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((windowClients) => {
        for (const client of windowClients) {
          if (new URL(client.url).origin === self.location.origin) {
            return Promise.resolve(client.focus()).then(() =>
              client.navigate(targetUrl),
            );
          }
        }
        return self.clients.openWindow(targetUrl);
      }),
  );
});
