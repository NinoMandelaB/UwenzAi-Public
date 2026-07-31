// src/pages/details/UserPreferences.jsx

import { useState, useEffect } from "react";
import { Clock, Info } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";
import { useAuth } from "../../context/AuthContext";
import {
  getProactivePreference,
  updateProactivePreference,
} from "../../services/conversationService";
import { clearMemoryForCharacter } from "../../services/memoryService";
import ConfirmDialog from "../../components/ConfirmDialog";

export default function UserPreferences({ characterId }) {
  const { t } = useLanguage();
  const { isAuthenticated } = useAuth();

  // Preference states
  const [prefLoading, setPrefLoading] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [intervalHours, setIntervalHours] = useState(24);

  // Duration modal
  const [showDurationModal, setShowDurationModal] = useState(false);
  const [tempInterval, setTempInterval] = useState("24"); // string now
  const [isUpdating, setIsUpdating] = useState(false);
  const [durationError, setDurationError] = useState("");

  // Clear memory modal
  const [showClearModal, setShowClearModal] = useState(false);
  const [isClearing, setIsClearing] = useState(false);
  const [clearError, setClearError] = useState("");

  // Fetch preference on mount
  useEffect(() => {
    if (!isAuthenticated || !characterId) return;

    const fetchPref = async () => {
      setPrefLoading(true);
      try {
        const data = await getProactivePreference(characterId);
        setEnabled(data.enabled);
        setIntervalHours(data.interval_hours);
        setTempInterval(String(data.interval_hours));
      } catch (err) {
        console.error("Failed to fetch proactive preference:", err);
      } finally {
        setPrefLoading(false);
      }
    };

    fetchPref();
  }, [characterId, isAuthenticated]);

  if (!isAuthenticated) return null;

  const handleToggle = () => {
    if (enabled) {
      handleUpdatePreference(false, intervalHours);
    } else {
      setTempInterval(String(intervalHours));
      setDurationError("");
      setShowDurationModal(true);
    }
  };

  const handleUpdatePreference = async (newEnabled, newInterval) => {
    setIsUpdating(true);
    try {
      const data = await updateProactivePreference(
        characterId,
        newEnabled,
        newInterval,
      );
      setEnabled(data.enabled);
      setIntervalHours(data.interval_hours);
      setTempInterval(String(data.interval_hours));
      setShowDurationModal(false);
    } catch (err) {
      console.error("Failed to update proactive preference:", err);
      setDurationError(err?.message || t("common.failedToUpdatePreference"));
    } finally {
      setIsUpdating(false);
    }
  };

  const handleConfirmDuration = () => {
    // Parse the string to number
    const parsed = parseInt(tempInterval, 10);
    if (isNaN(parsed)) {
      setDurationError(t("common.intervalMustBeNumber"));
      return;
    }
    if (parsed < 6 || parsed > 168) {
      setDurationError(t("common.intervalMustBeBetween", { min: 6, max: 168 }));
      return;
    }
    setDurationError("");
    handleUpdatePreference(true, parsed);
  };

  const handleClearMemory = async () => {
    setIsClearing(true);
    setClearError("");
    try {
      await clearMemoryForCharacter(characterId);
      setShowClearModal(false);
    } catch (err) {
      setClearError(err?.message || t("common.failedToClearMemory"));
    } finally {
      setIsClearing(false);
    }
  };

  return (
    <div
      className="rounded-2xl border p-4 mt-6"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-card)",
      }}
    >
      <div className="flex flex-col gap-4">
        {/* Interactive Chat Section */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold theme-text">
                {t("common.interactiveChat")}
              </span>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground transition"
                onClick={() => alert(t("common.interactiveChatInfo"))}
              >
                <Info size={16} />
              </button>
            </div>
            <div className="flex items-center gap-3">
              <span
                className={`text-xs font-medium ${
                  enabled ? "text-[var(--color-accent)]" : "text-gray-400"
                }`}
              >
                {enabled ? "On" : "Off"}
              </span>
              <label className="relative inline-flex cursor-pointer items-center">
                <input
                  type="checkbox"
                  className="peer sr-only"
                  checked={enabled}
                  onChange={handleToggle}
                  disabled={prefLoading || isUpdating}
                />
                <div
                  className={`flex h-7 w-12 items-center rounded-full transition-colors duration-200 ease-in-out ${
                    enabled
                      ? "bg-[var(--color-accent)]"
                      : "bg-gray-300 dark:bg-gray-600"
                  } ${
                    prefLoading || isUpdating
                      ? "opacity-50 cursor-not-allowed"
                      : ""
                  }`}
                >
                  <div
                    className={`h-6 w-6 transform rounded-full bg-white shadow-md transition-transform duration-200 ease-in-out ${
                      enabled ? "translate-x-5" : "translate-x-0.5"
                    }`}
                  />
                </div>
              </label>
            </div>
          </div>

          <p className="text-xs theme-text-muted">
            {t("common.interactiveChatNote")}
          </p>
          {enabled && (
            <div className="flex items-center gap-1 text-xs theme-text-muted">
              <Clock size={14} />
              <span>
                {t("common.intervalConfigured", { hours: intervalHours })}
              </span>
            </div>
          )}
        </div>

        {/* Divider */}
        <div
          className="border-t"
          style={{ borderColor: "var(--color-border)" }}
        />

        {/* Clear Memory Section */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold theme-text">
              {t("common.clearMemory")}
            </span>
            <button
              type="button"
              onClick={() => setShowClearModal(true)}
              className="rounded-xl px-3 py-1.5 text-xs font-medium transition hover:opacity-80"
              style={{
                backgroundColor: "rgba(239, 68, 68, 0.1)",
                color: "#ef4444",
                border: "1px solid rgba(239, 68, 68, 0.2)",
              }}
            >
              {t("common.clear")}
            </button>
          </div>
          <p className="text-xs theme-text-muted">
            {t("common.clearMemoryExplanation")}
          </p>
        </div>
      </div>

      {/* Duration Modal */}
      {showDurationModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
          onClick={(e) => {
            if (e.target === e.currentTarget && !isUpdating)
              setShowDurationModal(false);
          }}
        >
          <div
            className="relative w-full max-w-md rounded-2xl p-6 shadow-xl"
            style={{
              backgroundColor: "var(--color-shell)",
              border: "1px solid var(--color-border)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-xl font-semibold theme-text mb-2">
              {t("common.enableInteractiveChat")}
            </h3>
            <p className="text-sm theme-text-muted mb-4">
              {t("common.interactiveChatDurationExplanation")}
            </p>

            <div className="mb-4">
              <label
                htmlFor="interval"
                className="block text-xs font-bold uppercase tracking-wider theme-text-muted mb-1.5"
              >
                {t("common.intervalHoursLabel")}
              </label>
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                id="interval"
                value={tempInterval}
                onChange={(e) => {
                  // Allow only digits
                  const val = e.target.value.replace(/[^0-9]/g, "");
                  setTempInterval(val);
                  setDurationError("");
                }}
                className="w-full rounded-xl border px-4 py-2.5 text-sm outline-none transition focus:ring-2"
                style={{
                  borderColor: "var(--color-border)",
                  backgroundColor: "var(--color-input)",
                  color: "var(--color-text)",
                }}
                placeholder="Enter hours"
              />
              <p className="mt-1 text-xs theme-text-muted">
                {t("common.intervalRangeHint", { min: 6, max: 168 })}
              </p>
              {durationError && (
                <p className="mt-1 text-xs text-red-500">{durationError}</p>
              )}
            </div>

            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setShowDurationModal(false)}
                className="flex-1 rounded-xl border px-4 py-2.5 text-sm font-medium transition hover:bg-[var(--color-soft)]"
                style={{
                  borderColor: "var(--color-border)",
                  color: "var(--color-text-muted)",
                }}
                disabled={isUpdating}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                onClick={handleConfirmDuration}
                disabled={isUpdating}
                className="flex-1 rounded-xl px-4 py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                style={{ backgroundColor: "var(--color-accent)" }}
              >
                {isUpdating ? t("common.saving") : t("common.confirm")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Clear Memory Confirmation Dialog */}
      <ConfirmDialog
        isOpen={showClearModal}
        title={t("common.clearMemoryConfirmTitle")}
        message={
          <>
            <p>{t("common.clearMemoryConfirmMessage")}</p>
            {clearError && (
              <p className="mt-2 text-sm text-red-500">{clearError}</p>
            )}
          </>
        }
        confirmText={t("common.clear")}
        isLoading={isClearing}
        onConfirm={handleClearMemory}
        onClose={() => {
          setShowClearModal(false);
          setClearError("");
        }}
      />
    </div>
  );
}
