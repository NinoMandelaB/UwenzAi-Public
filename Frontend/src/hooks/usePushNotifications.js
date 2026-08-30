// src/hooks/usePushNotifications.js

import { useCallback, useEffect, useState } from "react";

import {
  getPushSubscription,
  isPushSupported,
  subscribeToPush,
  unsubscribeFromPush,
} from "../services/pushService";

/**
 * Push notification state for the signed-in user.
 *
 * The browser's PushManager is the source of truth: on mount the hook reads
 * the live subscription rather than trusting a stored flag, so state stays
 * correct across reinstalls, revoked permissions and other devices.
 */
export function usePushNotifications() {
  const [supported] = useState(isPushSupported);
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  // "granted" | "denied" | "default" — "denied" means the user must unblock
  // notifications in browser settings; the app cannot re-ask.
  const [permission, setPermission] = useState(
    supported ? Notification.permission : "default",
  );

  useEffect(() => {
    if (!supported) return;
    let cancelled = false;
    getPushSubscription().then((subscription) => {
      if (!cancelled) setEnabled(Boolean(subscription));
    });
    return () => {
      cancelled = true;
    };
  }, [supported]);

  const setPushEnabled = useCallback(
    async (wantEnabled) => {
      if (!supported || busy) return false;
      setBusy(true);
      try {
        if (wantEnabled) {
          await subscribeToPush();
          setEnabled(true);
        } else {
          await unsubscribeFromPush();
          setEnabled(false);
        }
        setPermission(Notification.permission);
        return true;
      } catch {
        setPermission(Notification.permission);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [supported, busy],
  );

  return { supported, enabled, busy, permission, setPushEnabled };
}
