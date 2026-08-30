// src/context/UnreadContext.jsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useWebSocket } from "../hooks/useWebSocket";
import { useAppSocketTarget } from "../hooks/useAppSocketTarget";
import { useAuth } from "./AuthContext";
import {
  getUnreadSummary,
  markConversationRead,
} from "../services/conversationService";
import { syncAppBadge } from "../services/appBadge";
import { ensureGuestSession } from "../utils/session";

const UnreadContext = createContext(null);

function sumCounts(counts) {
  return Object.values(counts).reduce((total, count) => total + count, 0);
}

/**
 * Unread badges for the chat list and the messages nav tab.
 *
 * The server owns the numbers; this only mirrors them. Marking read updates
 * the local map straight away so the badge disappears on tap, but the value
 * that survives is whatever the server pushes back.
 */
export function UnreadProvider({ children }) {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();
  const [counts, setCounts] = useState({});

  const applyUpdate = useCallback((conversationId, unreadCount) => {
    setCounts((prev) => {
      if (unreadCount > 0) return { ...prev, [conversationId]: unreadCount };
      if (!(conversationId in prev)) return prev;
      const next = { ...prev };
      delete next[conversationId];
      return next;
    });
  }, []);

  // The conversation list returns the same counts the summary endpoint does, so
  // loading it is a free reconciliation point: adopt those numbers rather than
  // firing a second request that could answer differently.
  const seed = useCallback((entries) => {
    setCounts(
      Object.fromEntries(
        entries
          .filter(([, count]) => count > 0)
          .map(([conversationId, count]) => [conversationId, count]),
      ),
    );
  }, []);

  const onMessage = useCallback(
    (data) => {
      if (data.type !== "unread_update" || !data.conversation_id) return;
      applyUpdate(data.conversation_id, data.unread_count || 0);
    },
    [applyUpdate],
  );

  const wsTarget = useAppSocketTarget();
  useWebSocket(wsTarget, { onMessage });

  const refresh = useCallback(async () => {
    try {
      if (!isAuthenticated) await ensureGuestSession();
      const summary = await getUnreadSummary();
      setCounts(
        Object.fromEntries(
          (summary?.conversations || []).map((row) => [
            row.conversation_id,
            row.unread_count,
          ]),
        ),
      );
    } catch (err) {
      // A missing badge is not worth surfacing to the user; the next push or
      // the next chat-list load reconciles it.
      console.warn("[Unread] could not load unread counts:", err.message);
    }
  }, [isAuthenticated]);

  // The counts belong to whoever is signed in, so a login or logout replaces
  // them wholesale rather than merging into the previous owner's map.
  const lastFetchedAuth = useRef(null);
  useEffect(() => {
    if (isAuthLoading) return;
    if (lastFetchedAuth.current === isAuthenticated) return;
    lastFetchedAuth.current = isAuthenticated;
    setCounts({});
    refresh();
  }, [isAuthenticated, isAuthLoading, refresh]);

  const markRead = useCallback(
    async (conversationId) => {
      if (!conversationId) return;
      applyUpdate(conversationId, 0);
      try {
        await markConversationRead(conversationId);
      } catch (err) {
        console.warn("[Unread] could not mark conversation read:", err.message);
      }
    },
    [applyUpdate],
  );

  const totalUnread = sumCounts(counts);

  // The installed app's icon carries the same total (Badging API). A closed
  // app gets its badge from the push service worker instead; this sync covers
  // every foreground change — socket updates, reads, sign-in/out.
  useEffect(() => {
    syncAppBadge(totalUnread);
  }, [totalUnread]);

  const value = useMemo(
    () => ({
      counts,
      totalUnread,
      unreadFor: (conversationId) => counts[conversationId] || 0,
      markRead,
      refresh,
      seed,
    }),
    [counts, totalUnread, markRead, refresh, seed],
  );

  return (
    <UnreadContext.Provider value={value}>{children}</UnreadContext.Provider>
  );
}

export function useUnread() {
  const context = useContext(UnreadContext);
  if (!context)
    throw new Error("useUnread must be used within an UnreadProvider");
  return context;
}
