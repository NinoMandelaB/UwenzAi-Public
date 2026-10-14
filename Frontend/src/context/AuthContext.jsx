// src/context/AuthContext.jsx

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  setAccessToken,
  clearAccessToken,
  refreshAccessToken,
  getAccessToken,
  apiFetch,
  setSessionExpiredCallback, // <-- NEW
  invalidateAuthRequests,
} from "../services/apiClient";
import {
  clearStoredAuth,
  getStoredUser,
  signoutUser,
  departingAccessToken,
  getFeaturePreferences,
  updateFeaturePreferences,
} from "../services/authService";
import { API, STORAGE_KEYS } from "../config/constants";
import { ensureGuestSession, resetGuestSession } from "../utils/session";
import { recordVisit } from "../utils/visitTracking";
import {
  reconcilePushSubscription,
  unsubscribePushLocally,
} from "../services/pushService";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(getStoredUser());
  const [accessToken, setAccessTokenState] = useState(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const session = useRef({ generation: 0, user: getStoredUser() });
  const [sessionGeneration, setSessionGeneration] = useState(0);
  const preferenceRequest = useRef(0);

  const syncUserState = (nextUser) => {
    session.current.user = nextUser || null;
    setUser(nextUser || null);
    setIsAuthenticated(Boolean(nextUser));
  };

  const syncAccessToken = (token) => {
    const nextToken = token || null;
    setAccessTokenState(nextToken);
    setAccessToken(nextToken);
  };

  const setAuthSession = ({ user: nextUser, accessToken: nextAccessToken }) => {
    invalidateAuthRequests();
    session.current.generation += 1;
    setSessionGeneration(session.current.generation);
    if (nextUser) {
      localStorage.setItem(STORAGE_KEYS.AUTH_USER, JSON.stringify(nextUser));
      syncUserState(nextUser);
    } else {
      localStorage.removeItem(STORAGE_KEYS.AUTH_USER);
      syncUserState(null);
    }
    syncAccessToken(nextAccessToken || null);
  };

  const clearAuthState = () => {
    invalidateAuthRequests();
    session.current.generation += 1;
    setSessionGeneration(session.current.generation);
    clearStoredAuth();
    syncUserState(null);
    syncAccessToken(null);
    // Local retirement must not wait on any network call.
    void unsubscribePushLocally().catch(() => {});
  };

  // The shared preference action owns identity checks; a captured profile can
  // never restore auth. A newer GET/PATCH supersedes any older request.
  const syncFeaturePreferences = useCallback(
    async (updates, { signal } = {}) => {
      const scope = { ...session.current };
      if (!scope.user?.user_id || !getAccessToken()) return null;
      const request = ++preferenceRequest.current;
      const options = { signal };
      const data =
        updates === undefined
          ? await getFeaturePreferences(options)
          : await updateFeaturePreferences(updates, options);
      if (
        signal?.aborted ||
        request !== preferenceRequest.current ||
        scope.generation !== session.current.generation ||
        scope.user.user_id !== session.current.user?.user_id
      )
        return null;
      const prefs = data?.data?.feature_preferences;
      if (!prefs || typeof prefs !== "object" || Array.isArray(prefs)) {
        throw new Error("Invalid feature preferences response.");
      }
      const validated = Object.fromEntries(
        ["reminders", "deepthink", "websearch", "memory_editor"]
          .filter((key) => typeof prefs[key] === "boolean")
          .map((key) => [key, prefs[key]]),
      );
      const nextUser = {
        ...session.current.user,
        feature_preferences: {
          ...session.current.user.feature_preferences,
          ...validated,
        },
      };
      session.current.user = nextUser;
      setUser(nextUser);
      localStorage.setItem(STORAGE_KEYS.AUTH_USER, JSON.stringify(nextUser));
      return nextUser.feature_preferences;
    },
    [],
  );

  const refreshCurrentUser = async () => {
    const generation = session.current.generation;
    const preferencesAtStart = preferenceRequest.current;
    try {
      const tokenInMemory = getAccessToken();
      if (!tokenInMemory) {
        clearAuthState();
        return null;
      }

      const response = await apiFetch(API.AUTH.ME, {
        method: "GET",
        skipAuthRefresh: true,
      });
      if (generation !== session.current.generation) return null;

      let data = null;
      try {
        data = await response.json();
      } catch {
        data = null;
      }
      if (generation !== session.current.generation) return null;

      if ([401, 403].includes(response.status)) {
        clearAuthState();
        return null;
      }
      if (!response.ok || data?.status !== "success") return null;

      const nextUser = data?.data?.user || null;
      if (!nextUser) {
        clearAuthState();
        return null;
      }
      if (preferencesAtStart !== preferenceRequest.current) {
        nextUser.feature_preferences =
          session.current.user?.feature_preferences;
      }

      localStorage.setItem(STORAGE_KEYS.AUTH_USER, JSON.stringify(nextUser));
      syncUserState(nextUser);
      return nextUser;
    } catch {
      return null;
    }
  };

  const initializeAuth = async () => {
    const generation = session.current.generation;
    try {
      const storedUser = getStoredUser();
      if (!storedUser) {
        clearAuthState();
        return;
      }

      setUser(storedUser);

      const refreshed = await refreshAccessToken();
      if (generation !== session.current.generation) return;
      if (!refreshed?.accessToken) {
        clearAuthState();
        return;
      }

      setAuthSession({
        user: refreshed.user || storedUser,
        accessToken: refreshed.accessToken,
      });
    } catch {
      // Connectivity loss is not evidence of expired credentials. Keep the
      // stored profile/subscription for retry, without claiming authenticated.
      if (generation === session.current.generation) {
        syncUserState(null);
        syncAccessToken(null);
      }
    } finally {
      setIsLoading(false);
    }
  };

  const login = ({ user: nextUser, accessToken: nextAccessToken }) => {
    setAuthSession({
      user: nextUser,
      accessToken: nextAccessToken,
    });
  };

  const logout = async () => {
    // Renew a verifiably expired departing token (bounded), start cleanup with
    // that token, then invalidate locally immediately.
    const authToken = await departingAccessToken();
    const cleanup = signoutUser({ authToken });
    clearAuthState();
    resetGuestSession();
    try {
      return await cleanup;
    } catch {
      // Even if backend signout fails, clear client auth state.
    }
  };

  useEffect(() => {
    initializeAuth();
    return () => {
      session.current.generation += 1;
      clearAccessToken();
    };
  }, []);

  // Establish the guest credential (and retire any legacy localStorage ID) as
  // soon as we know nobody is signed in, so the first guest API call already has
  // a cookie to authenticate with.
  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      ensureGuestSession();
    }
  }, [isLoading, isAuthenticated]);

  // Audience analytics: record this app open as a visit once the auth state is
  // known (so an about-to-be-authenticated visitor is not misrecorded as a
  // guest), and again whenever a background tab becomes visible. Fire and
  // forget — see utils/visitTracking.js.
  useEffect(() => {
    if (isLoading) return undefined;
    recordVisit();

    function onVisibilityChange() {
      if (document.visibilityState === "visible") {
        recordVisit();
      }
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [isLoading]);

  useEffect(() => {
    if (isLoading || !isAuthenticated || !user?.user_id || !accessToken) return;
    reconcilePushSubscription().catch(() => {
      // Reconciliation fails closed by removing the local subscription.
      // Authentication itself must remain available.
    });
  }, [isLoading, isAuthenticated, user?.user_id, accessToken]);

  // ---------- Global session‑expiry handler ----------
  useEffect(() => {
    setSessionExpiredCallback(async () => {
      clearAuthState();
      resetGuestSession();
      await unsubscribePushLocally().catch(() => {});
      window.location.href = "/characters"; // full page redirect to public area
    });
    return () => setSessionExpiredCallback(null);
  }, []);

  const isAdmin = Boolean(user?.is_staff || user?.is_superuser);

  const value = useMemo(
    () => ({
      user,
      accessToken,
      isAuthenticated,
      isAdmin,
      isLoading,
      setUser: (nextUser) => {
        // Profile-editor callbacks must obey the same identity boundary.
        if (
          sessionGeneration !== session.current.generation ||
          !getAccessToken() ||
          !nextUser ||
          nextUser.user_id !== session.current.user?.user_id
        )
          return;
        syncUserState(nextUser);
      },
      setAuthSession,
      login,
      logout,
      refreshCurrentUser,
      clearAuthState,
      sessionGeneration,
      syncFeaturePreferences,
    }),
    [
      user,
      accessToken,
      isAuthenticated,
      isAdmin,
      isLoading,
      sessionGeneration,
      syncFeaturePreferences,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider.");
  }
  return context;
}
