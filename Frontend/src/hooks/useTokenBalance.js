// src/hooks/useTokenBalance.js

import { useState, useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import { ensureGuestSession } from "../utils/session";
import { apiFetch } from "../services/apiClient";
import { API } from "../config/constants";

export function useTokenBalance() {
  const { isAuthenticated, user } = useAuth();
  const [tokenBalance, setTokenBalance] = useState(null);
  const [guestRemaining, setGuestRemaining] = useState(null);

  const fetchBalance = async () => {
    try {
      if (!isAuthenticated) {
        await ensureGuestSession();
        const res = await apiFetch(API.TOKENS.BALANCE, {
          method: "GET",
          skipCsrf: true,
          skipAuthHeader: true,
        });
        const data = await res.json();
        if (data.status === "success") {
          setGuestRemaining({
            daily: data.data.daily_remaining,
            lifetime: data.data.lifetime_remaining,
          });
          setTokenBalance(null);
        }
      } else {
        const res = await apiFetch(API.TOKENS.BALANCE, {
          method: "GET",
          skipCsrf: true,
        });
        const data = await res.json();
        if (data.status === "success") {
          setTokenBalance(data.data.token_balance);
          setGuestRemaining(null);
        }
      }
    } catch (err) {
      console.warn("Token balance fetch failed", err);
    }
  };

  useEffect(() => {
    fetchBalance();
  }, [isAuthenticated, user]);

  // Also listen to the same window event used by the header to refresh after AI messages
  useEffect(() => {
    const handler = () => fetchBalance();
    window.addEventListener("token-balance-update", handler);
    return () => window.removeEventListener("token-balance-update", handler);
  }, [isAuthenticated]);

  return { tokenBalance, guestRemaining };
}
