// src/pages/NotFound.jsx
//
// Catch-all 404 route. Two jobs:
//
//   1. Give a human a friendly dead end with a way back into the app.
//   2. Give crawlers a real signal: the server still returns 200 for unknown
//      URLs (single-page app hosting), so this page sets a
//      `robots: noindex` meta tag while mounted. Search engines treat the
//      combination of 200 + noindex as a soft 404 and drop the URL from the
//      index instead of ranking dead pages.
//
// The meta tag is added on mount and removed on unmount so real pages are
// never accidentally noindexed.

import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useLanguage } from "../context/LanguageContext";

export default function NotFound() {
  const { t } = useLanguage();

  useEffect(() => {
    const previousTitle = document.title;
    document.title = `${t("notFound.title")} · UwenzAi`;

    let meta = document.querySelector('meta[name="robots"]');
    const hadMeta = meta !== null;
    if (!meta) {
      meta = document.createElement("meta");
      meta.setAttribute("name", "robots");
      document.head.appendChild(meta);
    }
    const previousContent = meta.getAttribute("content");
    meta.setAttribute("content", "noindex");

    return () => {
      document.title = previousTitle;
      if (hadMeta) {
        meta.setAttribute(
          "content",
          previousContent === null ? "" : previousContent,
        );
      } else {
        meta.remove();
      }
    };
  }, [t]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      style={{ backgroundColor: "var(--color-background, #0b0f19)" }}
      role="main"
      aria-labelledby="not-found-title"
    >
      <div
        className="w-full max-w-sm rounded-2xl p-6 text-center shadow-xl"
        style={{
          backgroundColor: "var(--color-card)",
          border: "1px solid var(--color-border)",
        }}
      >
        <div className="mb-3 text-4xl font-bold theme-text">404</div>

        <h1
          id="not-found-title"
          className="mb-2 text-lg font-semibold theme-text"
        >
          {t("notFound.title")}
        </h1>

        <p className="mb-6 text-sm theme-text-muted">{t("notFound.body")}</p>

        <Link
          to="/characters"
          className="block w-full rounded-xl px-4 py-3 text-sm font-medium transition hover:opacity-90"
          style={{
            backgroundColor: "var(--color-accent-soft)",
            color: "var(--color-accent)",
            border: "1px solid var(--color-accent-soft)",
          }}
        >
          {t("notFound.backHome")}
        </Link>
      </div>
    </div>
  );
}
