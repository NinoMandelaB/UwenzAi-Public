// src/hooks/usePricing.js
import { useEffect, useState } from "react";
import { getPricing } from "../services/billingService";

/**
 * Shared access to the backend `/pricing/` payload.
 *
 * Costs live in `capp/constants.py` and are served by the pricing endpoint, so
 * anything rendered from this hook tracks a backend price change with no
 * frontend edit. The in-flight request is cached at module scope: the image
 * prompt modals mount and unmount on every open, and several components can ask
 * for pricing at once, so without this each open would refire the request.
 *
 * Returns `{ pricing, isLoading, error }`. Callers must render prices only when
 * `pricing` is set — never fall back to a literal, or a stale hardcoded number
 * would be shown as if it were authoritative.
 */
let pricingPromise = null;

export function usePricing() {
  const [pricing, setPricing] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    if (!pricingPromise) {
      pricingPromise = getPricing().catch((err) => {
        // Drop the rejected promise so a later mount can retry rather than
        // replaying the same failure forever.
        pricingPromise = null;
        throw err;
      });
    }

    pricingPromise
      .then((res) => {
        if (cancelled) return;
        setPricing(res?.data || null);
        setIsLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error("usePricing error:", err);
        setError(err);
        setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return { pricing, isLoading, error };
}
