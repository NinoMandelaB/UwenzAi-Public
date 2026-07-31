// src/components/LoadOlderMessages.jsx

import { useLanguage } from "../context/LanguageContext";

/**
 * Top-of-transcript control for paging back through history.
 *
 * The button is the primary affordance and stays reachable by keyboard; the
 * scroll-near-top trigger in useChatScroll is a convenience on top of it, so
 * history is never trapped behind a gesture that failed to fire.
 */
export default function LoadOlderMessages({
  hasMore,
  isLoading,
  error,
  onLoadOlder,
  showStart = false,
}) {
  const { t } = useLanguage();

  if (isLoading) {
    return (
      <p
        className="py-2 text-center text-xs theme-text-muted"
        role="status"
        aria-live="polite"
      >
        {t("chat.loadingOlderMessages")}
      </p>
    );
  }

  if (error) {
    return (
      <div className="py-2 text-center" role="alert">
        <p className="text-xs text-amber-400">{t("chat.loadOlderFailed")}</p>
        <button
          type="button"
          onClick={onLoadOlder}
          className="mt-1 rounded-full px-3 py-1 text-xs font-medium underline theme-text-muted"
        >
          {t("chat.retryLoadOlder")}
        </button>
      </div>
    );
  }

  if (hasMore) {
    return (
      <div className="py-2 text-center">
        <button
          type="button"
          onClick={onLoadOlder}
          className="rounded-full border px-3 py-1 text-xs font-medium transition hover:opacity-90"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-soft)",
            color: "var(--color-text-muted)",
          }}
        >
          {t("chat.loadOlderMessages")}
        </button>
      </div>
    );
  }

  if (showStart) {
    return (
      <p className="py-2 text-center text-xs theme-text-muted">
        {t("chat.historyStart")}
      </p>
    );
  }

  return null;
}
