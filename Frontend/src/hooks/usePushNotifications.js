// src/hooks/usePushNotifications.js

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getPushSubscription,
  isPushSupported,
  subscribeToPush,
  unsubscribeFromPush,
  PUSH_STATE_CHANGED,
} from "../services/pushService";
import { boundedTask } from "../utils/boundedTask";

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
  const [cleanupOutcome, setCleanupOutcome] = useState(null);
  const [error, setError] = useState(null);
  const readGeneration = useRef(0);
  const mutationPending = useRef(false);
  // "granted" | "denied" | "default" — "denied" means the user must unblock
  // notifications in browser settings; the app cannot re-ask.
  const [permission, setPermission] = useState(
    supported ? Notification.permission : "default",
  );

  useEffect(() => {
    if (!supported) return;
    let cancelled = false;
    const refresh = () => {
      const generation = ++readGeneration.current;
      boundedTask(() => getPushSubscription())
        .then((subscription) => {
          if (!cancelled && generation === readGeneration.current)
            setEnabled(Boolean(subscription));
        })
        .catch(() => {});
    };
    refresh();
    window.addEventListener(PUSH_STATE_CHANGED, refresh);
    return () => {
      cancelled = true;
      window.removeEventListener(PUSH_STATE_CHANGED, refresh);
    };
  }, [supported]);

  const setPushEnabled = useCallback(
    async (wantEnabled) => {
      if (!supported || mutationPending.current) return false;
      mutationPending.current = true;
      readGeneration.current += 1;
      setBusy(true);
      setError(null);
      setCleanupOutcome(null);
      try {
        if (wantEnabled) {
          await subscribeToPush();
          setEnabled(true);
        } else {
          const outcome = await unsubscribeFromPush();
          setCleanupOutcome(outcome);
          setEnabled(!outcome.localRemoved);
          if (!outcome.localRemoved) setError("pushLocalCleanupError");
          else if (!outcome.remoteRemoved) setError("pushRemoteCleanupWarning");
          return outcome.localRemoved;
        }
        setPermission(Notification.permission);
        return true;
      } catch {
        setError("pushChangeError");
        setPermission(Notification.permission);
        return false;
      } finally {
        setPermission(Notification.permission);
        mutationPending.current = false;
        setBusy(false);
      }
    },
    [supported],
  );

  return {
    supported,
    enabled,
    busy,
    permission,
    error,
    cleanupOutcome,
    setPushEnabled,
  };
}
