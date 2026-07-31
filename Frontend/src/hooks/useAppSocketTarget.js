// src/hooks/useAppSocketTarget.js
import { useMemo } from "react";
import { useAuth } from "../context/AuthContext";
import { WS, WS_CHANNELS } from "../config/constants";
import { issueGuestWsTicket } from "../utils/session";

/**
 * The one socket that stays open on every screen.
 *
 * It started life carrying spark balances and now also carries unread badges,
 * because both are owner-wide facts that have to reach the user wherever they
 * are — including screens with no conversation socket of their own.
 *
 * Guests get a factory rather than a URL: the credential cookie is HttpOnly and
 * unreadable here, and putting anything long-lived in a WebSocket URL would
 * leak it into proxy logs. Each connection attempt mints a fresh single-use
 * ticket instead. Consumers share one connection by sharing this target's key.
 */
export function useAppSocketTarget() {
  const {
    isAuthenticated,
    user,
    accessToken,
    isLoading: isAuthLoading,
  } = useAuth();

  return useMemo(() => {
    if (isAuthLoading) return null;
    if (isAuthenticated && user && accessToken) {
      return WS.SPARK_POOL(accessToken, false);
    }
    if (isAuthenticated) return null;
    return {
      key: "spark_pool:guest",
      resolveUrl: async () => {
        const ticket = await issueGuestWsTicket(WS_CHANNELS.SPARK_POOL);
        return ticket ? WS.SPARK_POOL(ticket, true) : null;
      },
    };
  }, [isAuthLoading, isAuthenticated, user, accessToken]);
}
