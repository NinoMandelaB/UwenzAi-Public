// src/context/SparkPoolBalanceContext.jsx
import {
  createContext,
  useContext,
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
} from "react";
import { useWebSocket } from "../hooks/useWebSocket";
import { useAuth } from "./AuthContext";
import { API, WS, WS_CHANNELS } from "../config/constants";
import { ensureGuestSession, issueGuestWsTicket } from "../utils/session";
import { apiFetch } from "../services/apiClient";

const GUEST_CACHE_KEY = "guest_sparks_cache";
const AUTH_CACHE_KEY = "sparks_cache";

const SparkPoolBalanceContext = createContext(null);

export function SparkPoolBalanceProvider({ children }) {
  const {
    isAuthenticated,
    user,
    accessToken,
    isLoading: isAuthLoading,
  } = useAuth();

  const cacheKey = isAuthenticated ? AUTH_CACHE_KEY : GUEST_CACHE_KEY;

  // ── Balance state (initialised from cache) ──
  const [balance, setBalance] = useState(() => {
    const cached = localStorage.getItem(cacheKey);
    const initialBalance = cached ? parseInt(cached, 10) : null;
    console.log(
      "[SparkPoolBalance Context] Initial balance from cache:",
      initialBalance,
    );
    return initialBalance;
  });

  // ── WebSocket target ──
  //
  // Guests get a factory rather than a URL: the credential cookie is HttpOnly and
  // unreadable here, and putting anything long-lived in a WebSocket URL would
  // leak it into proxy logs. Each connection attempt mints a fresh single-use
  // ticket instead.
  const wsTarget = useMemo(() => {
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

  // ── WebSocket message handler ──
  const onMessage = useCallback(
    (data) => {
      console.log("[SparkPoolBalance Context] WebSocket received data:", data);
      if (
        data.type === "spark_pool_balance" &&
        typeof data.sparks === "number"
      ) {
        const newBalance = data.sparks;
        console.log(
          "[SparkPoolBalance Context] ✅ Updating balance to:",
          newBalance,
        );
        setBalance(newBalance);
        localStorage.setItem(cacheKey, String(newBalance));
        console.log(
          "[SparkPoolBalance Context] Balance updated in cache and state.",
        );
      } else {
        console.log(
          "[SparkPoolBalance Context] ⚠️ Received non-balance message or invalid data",
        );
      }
    },
    [cacheKey],
  );

  // ── Connect single WebSocket instance ──
  const { isConnected, status } = useWebSocket(wsTarget, { onMessage });

  // ── HTTP fetch (one‑time per auth state change) using constants.js URLs ──
  const lastFetchedAuth = useRef({ isAuthenticated: false, userId: null });
  useEffect(() => {
    if (isAuthLoading) return;
    const userId = user?.user_id || null;
    const currentAuth = { isAuthenticated, userId };

    if (
      lastFetchedAuth.current.isAuthenticated === isAuthenticated &&
      lastFetchedAuth.current.userId === userId
    ) {
      console.log(
        "[SparkPoolBalance Context] Auth state unchanged, skipping HTTP fetch",
      );
      return;
    }
    lastFetchedAuth.current = currentAuth;
    console.log(
      "[SparkPoolBalance Context] Auth state changed, fetching balance via HTTP...",
    );

    const fetchBalance = async () => {
      try {
        const options = { method: "GET", skipCsrf: true };

        if (isAuthenticated && user) {
          options.skipAuthHeader = false;
        } else {
          await ensureGuestSession();
          options.skipAuthHeader = true;
        }

        const response = await apiFetch(API.SPARK_POOL.BALANCE, options);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        if (data.status === "success") {
          const newBalance = data.data.sparks;
          console.log(
            "[SparkPoolBalance Context] HTTP fetch success, balance:",
            newBalance,
          );
          setBalance(newBalance);
          localStorage.setItem(cacheKey, String(newBalance));
        } else {
          console.log(
            "[SparkPoolBalance Context] HTTP fetch returned error:",
            data,
          );
        }
      } catch (err) {
        console.warn(
          "[SparkPoolBalance Context] HTTP fetch error:",
          err.message,
        );
      }
    };

    fetchBalance();
  }, [isAuthenticated, user, isAuthLoading, cacheKey]);

  return (
    <SparkPoolBalanceContext.Provider value={{ balance, isConnected, status }}>
      {children}
    </SparkPoolBalanceContext.Provider>
  );
}

export function useSparkPoolBalanceContext() {
  const context = useContext(SparkPoolBalanceContext);
  if (!context) {
    throw new Error(
      "useSparkPoolBalanceContext must be used within a SparkPoolBalanceProvider",
    );
  }
  return context;
}
