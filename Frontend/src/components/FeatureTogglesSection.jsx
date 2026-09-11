// Frontend/src/components/FeatureTogglesSection.jsx
//
// Settings → Advanced: per-account toggles for the optional chat features
// (reminders, deep thinking, web search, memory editor). Availability is separate
// from per-chat activation. Saved choices always override absent-key defaults.

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Bell, Brain, BrainCircuit, Globe } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { resolveFeaturePreferences } from "../utils/featurePreferences";

const FEATURES = [
  { key: "reminders", icon: Bell },
  { key: "deepthink", icon: BrainCircuit },
  { key: "websearch", icon: Globe },
  { key: "memory_editor", icon: Brain },
];

function ToggleRow({
  title,
  subtitle,
  checked,
  disabled,
  onChange,
  icon: Icon,
}) {
  return (
    <div className="rounded-2xl border p-4 theme-card">
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <div
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border"
            style={{
              backgroundColor: "var(--color-soft)",
              borderColor: "var(--color-border)",
              color: "var(--color-accent)",
            }}
          >
            <Icon size={18} />
          </div>

          <div className="min-w-0">
            <p className="text-sm font-semibold theme-text">{title}</p>
            <p className="mt-1 text-sm leading-6 theme-text-muted">
              {subtitle}
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={onChange}
          disabled={disabled}
          data-testid={`feature-toggle-${checked ? "on" : "off"}`}
          aria-pressed={checked}
          aria-label={title}
          className={`relative h-7 w-12 shrink-0 rounded-full transition disabled:cursor-not-allowed disabled:opacity-60 ${
            checked ? "bg-[var(--color-accent)]" : "bg-slate-500/60"
          }`}
        >
          <span
            className={`absolute top-1 h-5 w-5 rounded-full bg-white transition ${
              checked ? "left-6" : "left-1"
            }`}
          />
        </button>
      </div>
    </div>
  );
}

export default function FeatureTogglesSection() {
  const { user, sessionGeneration } = useAuth();
  return <ScopedFeatureToggles key={`${user?.user_id}:${sessionGeneration}`} />;
}

function ScopedFeatureToggles() {
  const { t } = useLanguage();
  const { user, syncFeaturePreferences } = useAuth();
  const controller = useRef(null);

  // Local mirror of the server state; starts from whatever the auth context
  // already knows so the rows render instantly on page load.
  const prefs = resolveFeaturePreferences(user);
  const [isLoading, setIsLoading] = useState(true);
  const [pendingKey, setPendingKey] = useState(null);
  const [error, setError] = useState("");

  // Refresh from the server once (covers stale localStorage / other devices).
  useEffect(() => {
    let cancelled = false;
    const abort = new AbortController();
    controller.current = abort;
    (async () => {
      try {
        await syncFeaturePreferences(undefined, { signal: abort.signal });
      } catch {
        // Non-fatal: the auth-context snapshot is a good enough fallback.
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      abort.abort();
    };
  }, [syncFeaturePreferences]);

  const handleToggle = async (key) => {
    if (pendingKey) return;
    const next = !prefs[key];
    setPendingKey(key);
    setError("");
    try {
      await syncFeaturePreferences(
        { [key]: next },
        { signal: controller.current.signal },
      );
    } catch {
      if (!controller.current.signal.aborted)
        setError(t("settings.featureToggleError"));
    } finally {
      if (!controller.current.signal.aborted) setPendingKey(null);
    }
  };

  return (
    <div className="space-y-3" data-testid="feature-toggles-section">
      {FEATURES.map(({ key, icon }) => (
        <ToggleRow
          key={key}
          icon={icon}
          title={t(`settings.feature_${key}`)}
          subtitle={t(`settings.feature_${key}_subtitle`)}
          checked={Boolean(prefs[key])}
          disabled={isLoading || Boolean(pendingKey)}
          onChange={() => handleToggle(key)}
        />
      ))}

      {error && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-2xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-500"
        >
          <AlertCircle size={16} className="shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
