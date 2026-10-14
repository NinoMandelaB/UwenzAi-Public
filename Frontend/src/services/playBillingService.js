// src/services/playBillingService.js
//
// Google Play Billing for the Android Trusted Web Activity.
//
// Inside the Bubblewrap TWA the Digital Goods API is available and backed by
// Google Play. Outside it (the plain website) every function here resolves to
// "not available" and the Paystack flow stays exactly as it is.
//
// Purchase flow (see Chrome's TWA billing guide): the purchase itself goes
// through the Digital Goods API — `service.buy()` where the store supports it,
// otherwise the PaymentRequest flow with `https://play.google.com/billing` as
// the payment method. The purchase token that comes back is verified by our
// backend against the Play Developer API before any sparks are credited, then
// the purchase is acknowledged server-side (the backend) and consumed locally
// so the same package can be bought again.

import { API } from "../config/constants";
import { apiFetch } from "./apiClient";

export const PLAY_BILLING_METHOD = "https://play.google.com/billing";

// Error codes surfaced to the UI. Anything else is a generic failure.
export const PLAY_ERRORS = {
  NOT_AVAILABLE: "play_not_available",
  USER_CANCELLED: "play_user_cancelled",
  VERIFY_FAILED: "play_verify_failed",
};

// Verify results meaning the Sparks are on the balance; only these purchases
// are consumed. "pending" and "cancelled" are verified but not credited.
const CREDITED_RESULTS = new Set(["credited", "already_credited"]);

export class PlayBillingError extends Error {
  constructor(code, cause) {
    super(code);
    this.code = code;
    this.cause = cause;
  }
}

// Cached detection result. `null` means "checked, not available"; the check
// runs at most once per page load so the Spark Pool page can call it freely.
let cachedServicePromise = null;

/**
 * Resolve the Google Play Digital Goods service, or null outside the TWA.
 */
export function getPlayBillingService() {
  if (cachedServicePromise === null) {
    cachedServicePromise = (async () => {
      if (
        typeof window === "undefined" ||
        typeof window.getDigitalGoodsService !== "function"
      ) {
        return null;
      }
      try {
        const service =
          await window.getDigitalGoodsService(PLAY_BILLING_METHOD);
        return service ?? null;
      } catch {
        // Throwing (or an unavailable provider) simply means "not the
        // Android app" — the normal website state, not an error.
        return null;
      }
    })();
  }
  return cachedServicePromise;
}

/** Test hook: forget the cached detection so a new one can run. */
export function resetPlayBillingServiceCache() {
  cachedServicePromise = null;
}

/**
 * Fetch Play product details for the given product ids.
 *
 * Returns a map productId -> { itemId, title, price, currency, value, type }.
 * Products missing from the map are not purchasable right now and must not
 * fall back to the Paystack price inside the app.
 */
export async function fetchPlayItemDetails(productIds) {
  const service = await getPlayBillingService();
  if (!service || !Array.isArray(productIds) || productIds.length === 0) {
    return {};
  }
  try {
    const details = await service.getDetails(productIds);
    const map = {};
    for (const item of details || []) {
      if (!item?.itemId) continue;
      map[item.itemId] = {
        itemId: item.itemId,
        title: item.title,
        description: item.description,
        // PaymentCurrencyAmount: { currency, value }
        price: item.price?.value ?? "",
        currency: item.price?.currency ?? "",
        type: item.type,
      };
    }
    return map;
  } catch {
    // No details means no Play purchases — never a Paystack fallback.
    return {};
  }
}

/**
 * Format a Play price for display ("KES 100.00", "USD 3.99", ...).
 */
export function formatPlayPrice(currency, value) {
  if (!currency || value === "" || value === null || value === undefined) {
    return null;
  }
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
    }).format(Number(value));
  } catch {
    return `${currency} ${value}`;
  }
}

/**
 * Launch the Play purchase sheet for one product and return the raw purchase.
 *
 * Uses `service.buy()` where the Digital Goods implementation provides it,
 * and the PaymentRequest flow documented for Chrome TWAs otherwise. The two
 * paths return the same thing we need: a purchase token for the backend.
 */
async function launchPlayPurchase(service, productId) {
  // Future-proof path: the newer Digital Goods surface exposes buy() directly.
  if (typeof service.buy === "function") {
    const purchase = await service.buy(productId);
    const purchaseToken = purchase?.purchaseToken ?? purchase?.token ?? null;
    if (!purchaseToken) {
      throw new PlayBillingError(PLAY_ERRORS.VERIFY_FAILED);
    }
    return {
      purchaseToken,
      orderId: purchase?.orderId ?? null,
      complete: null,
    };
  }

  // Chrome TWA path: PaymentRequest with the Play billing method.
  const request = new PaymentRequest(
    [{ supportedMethods: PLAY_BILLING_METHOD, data: { sku: productId } }],
    // The total is required by the API but ignored by Play Billing; the
    // actual price comes from the Play Console configuration.
    { total: { label: "Total", amount: { currency: "USD", value: "0" } } },
  );

  let response;
  try {
    response = await request.show();
  } catch (err) {
    // A dismissed sheet rejects with an AbortError — the user changed their
    // mind, which is a normal outcome and must not look like a failure.
    if (err?.name === "AbortError") {
      throw new PlayBillingError(PLAY_ERRORS.USER_CANCELLED, err);
    }
    throw err;
  }

  const details = response?.details || {};
  const purchaseToken = details.purchaseToken ?? details.token ?? null;
  if (!purchaseToken) {
    try {
      await response.complete("fail");
    } catch {
      /* the UI must close even if the payment response is unusable */
    }
    throw new PlayBillingError(PLAY_ERRORS.VERIFY_FAILED);
  }

  // complete() closes the billing sheet. It is always called exactly once,
  // success or failure, so the sheet can never stay stuck open.
  const complete = (outcome) => response.complete(outcome).catch(() => {});
  return { purchaseToken, orderId: details.orderId ?? null, complete };
}

/**
 * Ask our backend to verify a purchase token and credit the sparks.
 * Resolves with the parsed response data, rejects with an Error.
 */
export async function verifyPlayPurchase({
  productId,
  purchaseToken,
  orderId = null,
}) {
  let response;
  try {
    response = await apiFetch(API.PLAY_BILLING.VERIFY, {
      method: "POST",
      body: {
        product_id: productId,
        purchase_token: purchaseToken,
        ...(orderId ? { order_id: orderId } : {}),
      },
    });
  } catch (err) {
    throw new PlayBillingError(PLAY_ERRORS.VERIFY_FAILED, err);
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok || body?.status !== "success") {
    throw new PlayBillingError(PLAY_ERRORS.VERIFY_FAILED, {
      status: response.status,
      data: body?.data,
    });
  }
  return body.data || {};
}

/**
 * Mark a purchase as consumed so the same package can be bought again.
 *
 * Best-effort by design: if it fails the sparks are already safely credited
 * and acknowledged server-side, and the recovery sweep on the next app open
 * consumes any lingering token.
 */
export async function consumePlayPurchase(service, purchaseToken) {
  if (!service || !purchaseToken) return false;
  try {
    await service.consume(purchaseToken);
    return true;
  } catch {
    return false;
  }
}

/**
 * Full purchase flow for one Spark package: buy → backend verify → consume.
 *
 * Resolves with { result, sparksGranted, balance } where result is
 * "credited" or "already_credited" (Sparks are on the balance), "pending"
 * (Google has not taken the money yet) or "cancelled" (nothing was bought).
 * Only credited purchases are consumed. Rejects with PlayBillingError —
 * USER_CANCELLED when the user dismissed the Play sheet.
 */
export async function purchaseSparksViaPlay({ service, productId }) {
  if (!service || !productId) {
    throw new PlayBillingError(PLAY_ERRORS.NOT_AVAILABLE);
  }

  const { purchaseToken, orderId, complete } = await launchPlayPurchase(
    service,
    productId,
  );

  let outcome;
  try {
    outcome = await verifyPlayPurchase({ productId, purchaseToken, orderId });
  } catch (err) {
    if (complete) await complete("fail");
    throw err;
  }

  const result = outcome.result ?? "credited";
  if (CREDITED_RESULTS.has(result)) {
    // Sparks are credited. Consume the purchase so the same product can be
    // bought again (consumables), then close the billing UI with success.
    await consumePlayPurchase(service, purchaseToken);
    if (complete) await complete("success");
  } else if (complete) {
    // Not credited, so never consumed. "pending": a slow payment method has
    // not completed yet; the purchase stays open and the recovery on the
    // next app open credits it once Google confirms the payment, so the
    // outcome is "unknown" rather than success or failure. "cancelled":
    // nothing was bought.
    await complete(result === "pending" ? "unknown" : "fail");
  }

  return {
    result,
    sparksGranted: outcome.sparks_granted ?? 0,
    balance: outcome.spark_pool_balance ?? null,
  };
}

// The recovery run in progress, shared by overlapping callers.
let recoveryInFlight = null;

/**
 * Recover purchases that completed but were never verified.
 *
 * If the app died between the Play sheet and the backend call, the purchase
 * sits there uncredited and unacknowledged (Google auto-refunds those after
 * three days). On the next app open, every outstanding purchase is verified
 * on the backend — which is idempotent — and consumed when credited. The
 * same applies to a pending purchase whose payment has since completed.
 *
 * Runs once at a time: callers that overlap (app open and the Spark Pool
 * page) share the run in progress instead of verifying every token twice.
 */
export function recoverPendingPlayPurchases() {
  if (recoveryInFlight === null) {
    recoveryInFlight = runPlayPurchaseRecovery().finally(() => {
      recoveryInFlight = null;
    });
  }
  return recoveryInFlight;
}

async function runPlayPurchaseRecovery() {
  const service = await getPlayBillingService();
  if (!service) return { recovered: 0 };

  let purchases = [];
  try {
    purchases = (await service.listPurchases()) || [];
  } catch {
    return { recovered: 0 };
  }

  let recovered = 0;
  for (const purchase of purchases) {
    const productId = purchase?.itemId;
    const purchaseToken = purchase?.purchaseToken;
    if (!productId || !purchaseToken) continue;
    try {
      const outcome = await verifyPlayPurchase({ productId, purchaseToken });
      if (CREDITED_RESULTS.has(outcome.result)) {
        await consumePlayPurchase(service, purchaseToken);
        recovered += 1;
      }
    } catch {
      // Unknown product or a token the backend refuses — skip; the daily
      // server-side reconciliation is the safety net for refunds anyway.
    }
  }
  return { recovered };
}
