// src/components/wizard/StepPersonality.jsx

import { useState, useEffect } from "react";
import { useWizard } from "../../context/WizardContext";
import { useLanguage } from "../../context/LanguageContext";
import { listPersonalities } from "../../services/personalityService";
import PageCard from "../PageCard";
import { Loader2 } from "lucide-react";

const MAX_PERSONALITIES = 3;

export default function StepPersonality() {
  const { state, updateState } = useWizard();
  const { t } = useLanguage();

  const [personalities, setPersonalities] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  // Fetch personalities from backend
  useEffect(() => {
    async function fetchPersonalities() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await listPersonalities();
        setPersonalities(response?.data?.personalities || []);
      } catch (err) {
        console.error("StepPersonality fetch error:", err);
        setError(t("wizard.personalitiesLoadError"));
      } finally {
        setIsLoading(false);
      }
    }
    fetchPersonalities();
  }, [t]);

  // Toggle a personality ID in the selection array, respecting the max limit
  const selectedIds = state.personality_ids || [];

  const togglePersonality = (id) => {
    let nextIds;
    if (selectedIds.includes(id)) {
      nextIds = selectedIds.filter((pid) => pid !== id);
    } else {
      if (selectedIds.length >= MAX_PERSONALITIES) {
        return;
      }
      nextIds = [...selectedIds, id];
    }
    updateState({ personality_ids: nextIds });
  };

  // Loading state
  if (isLoading) {
    return (
      <PageCard className="flex flex-col items-center justify-center py-10">
        <Loader2 className="animate-spin text-cyan-400 mb-3" size={32} />
        <p className="text-sm theme-text-muted">
          {t("wizard.loadingPersonalities")}
        </p>
      </PageCard>
    );
  }

  // Error state
  if (error) {
    return (
      <PageCard className="text-center py-8">
        <p className="text-sm text-red-400">{error}</p>
      </PageCard>
    );
  }

  return (
    <PageCard className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold theme-text">
          {t("wizard.step5.title")}
        </h3>
        <p className="mt-1 text-sm theme-text-muted">
          {t("wizard.step5.subtitle")}
        </p>
      </div>

      {personalities.length === 0 ? (
        <p className="text-sm theme-text-muted">
          {t("wizard.noPersonalitiesAvailable")}
        </p>
      ) : (
        <>
          {selectedIds.length >= MAX_PERSONALITIES && (
            <div className="rounded-xl border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-300">
              {t("wizard.maxPersonalitiesReached")}
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {personalities.map((pers) => {
              const isSelected = selectedIds.includes(pers.id);
              const isDisabled =
                !isSelected && selectedIds.length >= MAX_PERSONALITIES;

              return (
                <button
                  key={pers.id}
                  type="button"
                  onClick={() => togglePersonality(pers.id)}
                  disabled={isDisabled}
                  className={`flex items-start gap-3 rounded-xl border p-4 text-left transition hover:border-cyan-400/50 ${
                    isDisabled ? "opacity-50 cursor-not-allowed" : ""
                  }`}
                  style={{
                    borderColor: isSelected
                      ? "var(--color-accent)"
                      : "var(--color-border)",
                    backgroundColor: isSelected
                      ? "var(--color-accent-soft)"
                      : "var(--color-card)",
                  }}
                >
                  <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent-soft)] text-sm font-bold text-[var(--color-accent)]">
                    {pers.name.charAt(0).toUpperCase()}
                  </span>

                  <div className="flex-1">
                    <div className="flex items-center justify-between">
                      <span
                        className="font-medium"
                        style={{
                          color: isSelected
                            ? "var(--color-accent)"
                            : "var(--color-text)",
                        }}
                      >
                        {pers.name}
                      </span>
                      {isSelected && (
                        <span
                          className="h-5 w-5 rounded-full"
                          style={{ backgroundColor: "var(--color-accent)" }}
                        >
                          <svg
                            className="h-5 w-5 text-white"
                            viewBox="0 0 20 20"
                            fill="currentColor"
                          >
                            <path
                              fillRule="evenodd"
                              d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                              clipRule="evenodd"
                            />
                          </svg>
                        </span>
                      )}
                    </div>
                    {pers.description && (
                      <p className="mt-1 text-xs theme-text-muted">
                        {pers.description}
                      </p>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </>
      )}
    </PageCard>
  );
}
