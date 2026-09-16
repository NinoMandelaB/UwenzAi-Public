// src/components/AgeGate.jsx
//
// One-time-per-session adult-content gate for guests.
//
// UwenzAi is an 18+ platform, so visitors must confirm their age before any
// app content (characters, chat, marketing pages) is shown. Behaviour:
//
//   - Authenticated users never see it: age is already confirmed during sign
//     up, so gating them again would be pure friction. The gate waits for auth
//     restoration (isLoading) so a logged-in user with a valid session never
//     gets a flash of the overlay.
//   - Guests see it once per browser session: confirmation is stored in
//     sessionStorage, so it survives in-app navigation and page reloads but
//     clears when the tab/browser is closed. sessionStorage (not
//     localStorage) is deliberate — the requirement is "once per session",
//     and not persisting long-term also avoids implying we track guest age.
//   - Declining sends the guest away from the app entirely (Google), rather
//     than rendering a dead-end page inside the app.
//   - Legal pages (/terms, /privacy, /responsible-use, /subprocessors) bypass
//     the gate: the gate's own legal line links to them, and a visitor must be
//     able to read the policies BEFORE consenting to anything. The pages are
//     plain text, so no adult content is exposed by letting them through.

import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";

// Routes a visitor can read without first passing the 18+ gate.
const LEGAL_PATH_RE = /^\/(terms|privacy|responsible-use|subprocessors)(\/|$)/;

// Keyed per session; the value itself is meaningless, presence = confirmed.
const STORAGE_KEY = "age_verified";

// Where under-18 visitors are sent. Kept neutral on purpose.
const EXIT_URL = "https://www.google.com";

export default function AgeGate({ children }) {
  const { isAuthenticated, isLoading } = useAuth();
  const { t } = useLanguage();
  const { pathname } = useLocation();

  // Initialize from storage so a confirming guest never sees the gate again
  // within this session, even across reloads.
  const [confirmed, setConfirmed] = useState(
    () => sessionStorage.getItem(STORAGE_KEY) === "1",
  );

  // An authenticated user is age-verified by definition (sign-up flow); if a
  // guest logs in mid-session, drop the gate state so a later logout in the
  // same tab doesn't resurrect an already-answered question.
  useEffect(() => {
    if (isAuthenticated) {
      sessionStorage.removeItem(STORAGE_KEY);
      setConfirmed(true);
    }
  }, [isAuthenticated]);

  const handleConfirm = () => {
    sessionStorage.setItem(STORAGE_KEY, "1");
    setConfirmed(true);
  };

  const handleDecline = () => {
    // Full navigation (not SPA routing): the guest should leave the app.
    window.location.href = EXIT_URL;
  };

  // While auth state is being restored we can't yet know whether this visitor
  // is a logged-in (pre-verified) user — render nothing rather than flashing
  // either the app or the gate. This matches the splash behaviour elsewhere.
  if (isLoading) {
    return null;
  }

  if (isAuthenticated || confirmed || LEGAL_PATH_RE.test(pathname)) {
    return children;
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      style={{ backgroundColor: "var(--color-background, #0b0f19)" }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="age-gate-title"
    >
      <div
        className="w-full max-w-sm rounded-2xl p-6 text-center shadow-xl"
        style={{
          backgroundColor: "var(--color-card)",
          border: "1px solid var(--color-border)",
        }}
      >
        <div className="mb-3 text-4xl font-bold theme-text">18+</div>

        <h1
          id="age-gate-title"
          className="mb-2 text-lg font-semibold theme-text"
        >
          {t("ageGate.title")}
        </h1>

        <p className="mb-6 text-sm theme-text-muted">{t("ageGate.body")}</p>

        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={handleConfirm}
            className="w-full rounded-xl px-4 py-3 text-sm font-medium transition hover:opacity-90"
            style={{
              backgroundColor: "var(--color-accent-soft)",
              color: "var(--color-accent)",
              border: "1px solid var(--color-accent-soft)",
            }}
          >
            {t("ageGate.confirm")}
          </button>
          <button
            type="button"
            onClick={handleDecline}
            className="w-full rounded-xl px-4 py-3 text-sm font-medium transition hover:opacity-90"
            style={{
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-soft)",
              color: "var(--color-text-muted)",
            }}
          >
            {t("ageGate.decline")}
          </button>
        </div>

        <p className="mt-4 text-xs theme-text-muted">
          {t("ageGate.legalLead")}{" "}
          <Link
            to="/terms"
            className="underline hover:opacity-80"
            style={{ color: "var(--color-accent)" }}
          >
            {t("ageGate.termsLink")}
          </Link>{" "}
          {t("ageGate.and")}{" "}
          <Link
            to="/privacy"
            className="underline hover:opacity-80"
            style={{ color: "var(--color-accent)" }}
          >
            {t("ageGate.privacyLink")}
          </Link>
          {t("ageGate.legalTail")}
        </p>
      </div>
    </div>
  );
}
