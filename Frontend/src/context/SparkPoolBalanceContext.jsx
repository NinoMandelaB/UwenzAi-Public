// src/context/SparkPoolBalanceContext.jsx
//
// The single source of the Sparks number shown in the header, the chat window
// and the Spark Pool page.
//
// `balance` is null whenever the number on screen would be a guess. Everything
// that renders it treats null as "still loading", because the old behaviour —
// falling back to zero — told first-time guests they were broke and then cached
// that zero for the next visit.
import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
} from "react";
import { useWebSocket } from "../hooks/useWebSocket";
import { useAppSocketTarget } from "../hooks/useAppSocketTarget";
import { useAuth } from "./AuthContext";
import { API } from "../config/constants";
import { ensureGuestSession, resetGuestSession } from "../utils/session";
import { apiFetch } from "../services/apiClient";

const GUEST_CACHE_KEY = "guest_sparks_cache";
const AUTH_CACHE_KEY = "sparks_cache";

const GUEST = "guest";

const SparkPoolBalanceContext = createContext(null);

const cacheKeyFor = (identity) =>
  identity === GUEST ? GUEST_CACHE_KEY : AUTH_CACHE_KEY;

/**
 * A previously seen balance, or null when there is nothing worth showing.
 *
 * Zero is deliberately read as "unknown". It is the value a failed read used to
 * write here, so a cached zero is more likely to be that bug's residue than a
 * guest who really has spent everything — and one round trip of "…" beats a
 * wrong number that sticks.
 */
function readCache(identity) {
  const cached = Number.parseInt(
    localStorage.getItem(cacheKeyFor(identity)),
    10,
  );
  return Number.isInteger(cached) && cached > 0 ? cached : null;
}

/**
 * Ask the server for the current balance.
 *
 * A guest is identified by an HttpOnly cookie this code cannot read, so the
 * cookie has to be minted before the request rather than alongside it. If the
 * server still rejects the request the credential is gone or expired, and one
 * fresh mint is worth trying before giving up on this attempt.
 */
async function requestBalance(isAuthenticated) {
  const options = {
    method: "GET",
    skipCsrf: true,
    skipAuthHeader: !isAuthenticated,
  };

  if (isAuthenticated) {
    return apiFetch(API.SPARK_POOL.BALANCE, options);
  }

  await ensureGuestSession();
  const response = await apiFetch(API.SPARK_POOL.BALANCE, options);
  if (response.status !== 401) {
    return response;
  }

  resetGuestSession();
  await ensureGuestSession();
  return apiFetch(API.SPARK_POOL.BALANCE, options);
}

export function SparkPoolBalanceProvider({ children }) {
  const { isAuthenticated, user, isLoading: isAuthLoading } = useAuth();

  // Who the number belongs to. Guest Sparks and account Sparks are separate
  // buckets — signing in moves one into the other — so a balance is only ever
  // valid for the identity it was read for.
  const identity = isAuthLoading
    ? null
    : isAuthenticated
      ? `user:${user?.user_id || ""}`
      : GUEST;

  const identityRef = useRef(identity);
  identityRef.current = identity;

  const [state, setState] = useState({
    identity: null,
    sparks: null,
    confirmed: false,
  });
  const inFlight = useRef(false);

  const apply = useCallback((forIdentity, sparks) => {
    // The owner can change while a request or a socket frame is in flight.
    if (forIdentity !== identityRef.current) return;
    localStorage.setItem(cacheKeyFor(forIdentity), String(sparks));
    setState({ identity: forIdentity, sparks, confirmed: true });
  }, []);

  const seenIdentity = useRef(null);
  useEffect(() => {
    if (identity === null || seenIdentity.current === identity) return;
    const isSwitch = seenIdentity.current !== null;
    seenIdentity.current = identity;

    if (isSwitch) {
      // Signing in, signing out and switching accounts all hand the screen to a
      // different owner. Both cached numbers are stale the moment that happens,
      // and the guest one is doubly so after a sign-in has transferred it.
      localStorage.removeItem(GUEST_CACHE_KEY);
      localStorage.removeItem(AUTH_CACHE_KEY);
    }
    setState({
      identity,
      sparks: isSwitch ? null : readCache(identity),
      confirmed: false,
    });
  }, [identity]);

  const refresh = useCallback(async () => {
    if (identity === null || inFlight.current) return;
    inFlight.current = true;
    try {
      const response = await requestBalance(isAuthenticated);
      const data = response.ok ? await response.json() : null;
      if (data?.status === "success" && typeof data.data?.sparks === "number") {
        apply(identity, data.data.sparks);
      }
    } catch (err) {
      console.warn("[SparkPoolBalance] balance fetch failed:", err.message);
    } finally {
      inFlight.current = false;
    }
  }, [identity, isAuthenticated, apply]);

  const onMessage = useCallback(
    (data) => {
      if (
        data.type === "spark_pool_balance" &&
        typeof data.sparks === "number"
      ) {
        apply(identityRef.current, data.sparks);
      }
    },
    [apply],
  );

  const wsTarget = useAppSocketTarget();
  const { isConnected, status } = useWebSocket(wsTarget, { onMessage });

  // One attempt per identity. It is not retried on a loop, but a failure does
  // not latch either: until the server has actually answered, coming back to
  // the tab is another chance to ask.
  useEffect(() => {
    if (state.identity !== identity || state.confirmed) return undefined;

    refresh();

    const retry = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", retry);
    document.addEventListener("visibilitychange", retry);
    return () => {
      window.removeEventListener("focus", retry);
      document.removeEventListener("visibilitychange", retry);
    };
  }, [identity, state.identity, state.confirmed, refresh]);

  const balance = state.identity === identity ? state.sparks : null;

  const value = useMemo(
    () => ({ balance, isConnected, status }),
    [balance, isConnected, status],
  );

  return (
    <SparkPoolBalanceContext.Provider value={value}>
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
