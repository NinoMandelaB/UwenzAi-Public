// src/context/AuthContext.jsx

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import {
  setAccessToken,
  clearAccessToken,
  refreshAccessToken,
  getAccessToken,
  apiFetch,
  setSessionExpiredCallback, // <-- NEW
} from "../services/apiClient";
import {
  clearStoredAuth,
  getStoredUser,
  signoutUser,
} from "../services/authService";
import { API, STORAGE_KEYS } from "../config/constants";
import { ensureGuestSession, resetGuestSession } from "../utils/session";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(getStoredUser());
  const [accessToken, setAccessTokenState] = useState(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  const syncUserState = (nextUser) => {
    setUser(nextUser || null);
    setIsAuthenticated(Boolean(nextUser));
  };

  const syncAccessToken = (token) => {
    const nextToken = token || null;
    setAccessTokenState(nextToken);
    setAccessToken(nextToken);
  };

  const setAuthSession = ({ user: nextUser, accessToken: nextAccessToken }) => {
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
    clearStoredAuth();
    syncUserState(null);
    syncAccessToken(null);
  };

  const refreshCurrentUser = async () => {
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

      let data = null;
      try {
        data = await response.json();
      } catch {
        data = null;
      }

      if (!response.ok || data?.status !== "success") {
        clearAuthState();
        return null;
      }

      const nextUser = data?.data?.user || null;
      if (!nextUser) {
        clearAuthState();
        return null;
      }

      localStorage.setItem(STORAGE_KEYS.AUTH_USER, JSON.stringify(nextUser));
      syncUserState(nextUser);
      return nextUser;
    } catch {
      clearAuthState();
      return null;
    }
  };

  const initializeAuth = async () => {
    try {
      const storedUser = getStoredUser();
      if (!storedUser) {
        clearAuthState();
        return;
      }

      setUser(storedUser);

      const refreshed = await refreshAccessToken();
      if (!refreshed?.accessToken) {
        clearAuthState();
        return;
      }

      setAuthSession({
        user: refreshed.user || storedUser,
        accessToken: refreshed.accessToken,
      });
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
    try {
      await signoutUser();
    } catch {
      // Even if backend signout fails, clear client auth state.
    } finally {
      clearAuthState();
      // The cookie may or may not have survived this session: it is cleared only
      // when logging in actually transferred a guest session into the account.
      // Dropping the cached bootstrap makes the next guest action ask the server
      // which it was, instead of assuming.
      resetGuestSession();
    }
  };

  useEffect(() => {
    initializeAuth();
    return () => {
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

  // ---------- Global session‑expiry handler ----------
  useEffect(() => {
    setSessionExpiredCallback(() => {
      clearStoredAuth(); // removes user & spark_pool from localStorage
      clearAccessToken(); // clears the in‑memory access token
      window.location.href = "/characters"; // full page redirect to public area
    });
  }, []);

  const isAdmin = Boolean(user?.is_staff || user?.is_superuser);

  const value = useMemo(
    () => ({
      user,
      accessToken,
      isAuthenticated,
      isAdmin,
      isLoading,
      setUser: syncUserState,
      setAuthSession,
      login,
      logout,
      refreshCurrentUser,
      clearAuthState,
    }),
    [user, accessToken, isAuthenticated, isAdmin, isLoading],
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
