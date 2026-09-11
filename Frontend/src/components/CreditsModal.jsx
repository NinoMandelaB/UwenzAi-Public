// src/components/CreditsModal.jsx
//
// Small, unobtrusive modal listing the people behind UwenzAi, what has
// shipped since the beta release (with the week it shipped), and what
// is coming next. Opened from a tiny link at the bottom of Settings.
// All content lives in src/config/credits.js — edit it there.

import { ExternalLink, Heart, Mail, Sparkles, X } from "lucide-react";

import { useLanguage } from "../context/LanguageContext";
import { CREDITS } from "../config/credits";

// Short month names per app language (kept local so tests stay stable).
const MONTHS = {
  en: [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ],
  sw: [
    "Jan",
    "Feb",
    "Mac",
    "Apr",
    "Mei",
    "Jun",
    "Jul",
    "Ago",
    "Sep",
    "Okt",
    "Nov",
    "Des",
  ],
};

// "2026-08-28" → "Week of Aug 24" / "Wiki ya 24 Ago" (week starting Monday).
function weekLabel(dateStr, language) {
  const date = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(date.getTime())) return "";

  const day = date.getDay(); // 0 = Sunday
  const monday = new Date(date);
  monday.setDate(date.getDate() - (day === 0 ? 6 : day - 1));

  const months = MONTHS[language] || MONTHS.en;
  const label = `${monday.getDate()} ${months[monday.getMonth()]}`;
  return language === "sw" ? `Wiki ya ${label}` : `Week of ${label}`;
}

// Friendly label for a contributor link, derived from the URL.
function linkLabel(url) {
  if (url.startsWith("mailto:")) return "Email";
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    const known = {
      "github.com": "GitHub",
      "linkedin.com": "LinkedIn",
      "fiverr.com": "Fiverr",
      "pro.fiverr.com": "Fiverr",
    };
    return known[host] || host;
  } catch {
    return "Link";
  }
}

// Normalizes `urls` (list) and the legacy single `url` into link objects.
function contributorLinks(contributor) {
  const raw = contributor.urls ?? (contributor.url ? [contributor.url] : []);
  return raw
    .map((entry) =>
      typeof entry === "string" ? { label: null, url: entry } : entry,
    )
    .filter((entry) => entry?.url)
    .map(({ label, url }) => ({ label: label || linkLabel(url), url }));
}

function localized(item, language) {
  return item?.[language] || item?.en || "";
}

// Groups shipped items by week (assumes newest-first ordering in
// credits.js, so same-week items are adjacent and get one shared label).
function groupByWeek(shipped, language) {
  const groups = [];
  for (const item of shipped) {
    const label = weekLabel(item.date, language);
    const last = groups[groups.length - 1];
    if (last && last.label === label) {
      last.items.push(item);
    } else {
      groups.push({ label, items: [item] });
    }
  }
  return groups;
}

export default function CreditsModal({ isOpen, onClose }) {
  const { t, language } = useLanguage();

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("credits.title")}
        className="max-h-[85vh] w-full max-w-sm overflow-y-auto rounded-2xl p-6 shadow-lg theme-card"
      >
        <div className="mb-4 flex items-start justify-between">
          <h3 className="text-lg font-semibold theme-text">
            {t("credits.title")}
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="rounded-full p-1 text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
          >
            <X size={20} />
          </button>
        </div>

        <p className="text-sm leading-6 theme-text-muted">
          {t("credits.description")}
        </p>

        <ul className="mt-4 space-y-3">
          {CREDITS.contributors.map((contributor) => {
            const role = contributor.role?.[language] || contributor.role?.en;
            const links = contributorLinks(contributor);

            return (
              <li
                key={contributor.name}
                className="rounded-xl border p-3"
                style={{
                  borderColor: "var(--color-border)",
                  backgroundColor: "var(--color-soft)",
                }}
              >
                <p className="text-sm font-semibold theme-text">
                  {contributor.name}
                </p>
                {role && (
                  <p className="mt-0.5 text-xs leading-5 theme-text-muted">
                    {role}
                  </p>
                )}

                {links.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {links.map(({ label, url }) => {
                      const isEmail = url.startsWith("mailto:");
                      return (
                        <a
                          key={url}
                          href={url}
                          {...(isEmail ? {} : { target: "_blank" })}
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[10px] font-medium theme-text-muted transition hover:opacity-80"
                          style={{ borderColor: "var(--color-border)" }}
                        >
                          {isEmail ? (
                            <Mail size={10} />
                          ) : (
                            <ExternalLink size={10} />
                          )}
                          {label}
                        </a>
                      );
                    })}
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        {/* What has shipped since the beta release — grouped by week */}
        {CREDITS.shipped?.length > 0 && (
          <section className="mt-6">
            <h4 className="text-xs font-semibold uppercase tracking-wide theme-text-muted">
              {t("credits.shippedTitle")}
            </h4>

            <ul className="mt-3 space-y-3">
              {groupByWeek(CREDITS.shipped, language).map((group) => (
                <li key={group.label}>
                  <p className="text-[10px] font-semibold uppercase tracking-wide theme-text-muted">
                    {group.label}
                  </p>
                  <ul className="mt-1 space-y-1">
                    {group.items.map((item) => (
                      <li
                        key={`${item.date}-${item.en}`}
                        className="text-sm leading-6 theme-text"
                      >
                        {localized(item, language)}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* What's coming next — benefit-first, no dates */}
        {CREDITS.roadmap?.length > 0 && (
          <section className="mt-6 border-t pt-4 theme-border">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide theme-text-muted">
              <Sparkles size={12} />
              {t("credits.nextTitle")}
            </h4>

            <ul className="mt-3 space-y-2">
              {CREDITS.roadmap.map((item) => (
                <li
                  key={item.en}
                  className="text-sm leading-6 theme-text-muted"
                >
                  {localized(item, language)}
                </li>
              ))}
            </ul>
          </section>
        )}

        {CREDITS.openSourceUrl && (
          <a
            href={CREDITS.openSourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-6 flex items-center justify-center gap-2 rounded-xl border p-3 text-xs font-medium theme-text-muted transition hover:opacity-80"
            style={{ borderColor: "var(--color-border)" }}
          >
            <ExternalLink size={14} />
            {t("credits.openSource")}
          </a>
        )}

        <p className="mt-4 flex items-center justify-center gap-1.5 text-xs theme-text-muted">
          <Heart size={12} className="text-rose-400" />
          {t("credits.madeIn")}
        </p>
      </div>
    </div>
  );
}
