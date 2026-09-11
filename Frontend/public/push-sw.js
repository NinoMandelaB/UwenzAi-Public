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

const RECEIPT_TIMEOUT_MS = 1000;

function sameOriginUrl(value) {
  try {
    const url = new URL(
      typeof value === "string" ? value : "/",
      self.location.origin,
    );
    return url.origin === self.location.origin
      ? `${url.pathname}${url.search}${url.hash}`
      : "/";
  } catch {
    return "/";
  }
}

function foregroundReceipt(client, payload) {
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    let timer;
    const finish = (handled) => {
      clearTimeout(timer);
      channel.port1.close();
      resolve(handled);
    };
    channel.port1.onmessage = (event) => {
      if (event.data?.type === "UWENZAI_PUSH_HANDLED") finish(true);
    };
    timer = setTimeout(() => finish(false), RECEIPT_TIMEOUT_MS);
    try {
      client.postMessage({ type: "UWENZAI_PUSH_RECEIPT", payload }, [
        channel.port2,
      ]);
    } catch {
      channel.port2.close();
      finish(false);
    }
  });
}

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
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return;

  const title = typeof payload.title === "string" ? payload.title : "UwenzAi";
  const url = sameOriginUrl(payload.url);
  // Present on new-message pushes; mirrors the in-app nav badge onto the
  // launcher icon (Badging API, where supported).
  const unreadCount = Number.isInteger(payload.unread_count)
    ? payload.unread_count
    : null;
  const options = {
    body: typeof payload.body === "string" ? payload.body : "",
    icon: "/pwa-192.png",
    badge: "/pwa-192.png",
    // Same-conversation pushes replace each other instead of stacking one
    // notification per message.
    tag: url,
    renotify: false,
    data: { url },
  };

  // Visibility is not delivery. Suppress only if a foreground client renders
  // this event's in-app alert and acknowledges via its private receipt port.
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then(async (windowClients) => {
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
        const visibleClients = windowClients.filter(
          (client) =>
            new URL(client.url).origin === self.location.origin &&
            client.visibilityState === "visible",
        );
        const receipts = await Promise.all(
          visibleClients.map((client) =>
            foregroundReceipt(client, {
              title,
              body: options.body,
              url,
              unread_count: unreadCount,
            }),
          ),
        );
        // A broken badge API must not hold a user-visible alert hostage.
        if (!receipts.some(Boolean))
          await self.registration.showNotification(title, options);
        void badgeUpdate;
      }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = sameOriginUrl(event.notification.data?.url);

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
