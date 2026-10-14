// src/utils/visitTracking.js
//
// Fire-and-forget visit recording for the admin audience view.
//
// One ping per app open, plus one whenever a background tab becomes visible
// again. The server coalesces writes itself (see
// Backend/capp/classes/analytics/visit_service.py), and a lost ping never
// affects the user — every failure here is swallowed.

import { API } from "../config/constants";
import { apiFetch } from "../services/apiClient";
import { ensureGuestSession } from "./session";

// Client-side floor so an idle tab re-pinging visibility changes does not
// spam the endpoint; the server's own coalesce window is the real limit.
const MIN_VISIT_INTERVAL_MS = 5 * 60 * 1000;

let lastPingAt = 0;
let inFlight = null;

async function ping() {
  try {
    // Guests need the credential cookie before a visit can be attributed; for
    // signed-in visitors this resolves immediately (no cookie work needed).
    await ensureGuestSession();
    await apiFetch(API.ANALYTICS.VISIT, {
      method: "POST",
      skipAuthRefresh: true,
    });
  } catch {
    // Offline, backend down or a rejected mint: this visit is simply not
    // recorded. Never surface anything to the user.
  }
}

export function recordVisit() {
  const now = Date.now();
  if (now - lastPingAt < MIN_VISIT_INTERVAL_MS) {
    return;
  }
  lastPingAt = now;
  if (!inFlight) {
    inFlight = ping().finally(() => {
      inFlight = null;
    });
  }
}
