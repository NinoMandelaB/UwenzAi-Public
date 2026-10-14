// src/components/PlayPurchaseRecovery.jsx
//
// Credits Google Play purchases that were paid but never verified, every
// time the Android app opens with a signed-in account. Without it, recovery
// only ran when the user happened to open the Spark Pool page, and Google
// refunds purchases that stay unacknowledged for three days.
//
// Renders nothing. Outside the Android app the Digital Goods API is absent
// and the recovery resolves immediately without any network call.
import { useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import { recoverPendingPlayPurchases } from "../services/playBillingService";

export default function PlayPurchaseRecovery() {
  const { isLoading, isAuthenticated, user, accessToken } = useAuth();
  const userId = user?.user_id;
  // Only whether a token exists matters: a refreshed access token is not a
  // new reason to verify every outstanding purchase again.
  const hasToken = Boolean(accessToken);

  useEffect(() => {
    // Play purchases belong to accounts only (the backend refuses guests).
    if (isLoading || !isAuthenticated || !userId || !hasToken) return;
    recoverPendingPlayPurchases().catch((err) =>
      console.error("Play purchase recovery failed", err),
    );
  }, [isLoading, isAuthenticated, userId, hasToken]);

  return null;
}
