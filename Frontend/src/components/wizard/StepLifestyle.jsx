// src/components/wizard/StepLifestyle.jsx

import { useState, useEffect } from "react";
import { useWizard } from "../../context/WizardContext";
import { useLanguage } from "../../context/LanguageContext";
import { listOccupations, listHobbies } from "../../services/characterService";
import PageCard from "../PageCard";
import { Loader2 } from "lucide-react";

const MAX_HOBBIES = 3;

export default function StepLifestyle() {
  const { state, updateState } = useWizard();
  const { t } = useLanguage();

  const [occupations, setOccupations] = useState([]);
  const [hobbies, setHobbies] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  // Fetch occupations and hobbies on mount
  useEffect(() => {
    async function fetchData() {
      try {
        setIsLoading(true);
        setError(null);

        const [occupationsRes, hobbiesRes] = await Promise.all([
          listOccupations(),
          listHobbies(),
        ]);

        setOccupations(occupationsRes?.data?.occupations || []);
        setHobbies(hobbiesRes?.data?.hobbies || []);
      } catch (err) {
        console.error("Failed to fetch lifestyle data:", err);
        setError(t("wizard.step6.loadError"));
      } finally {
        setIsLoading(false);
      }
    }

    fetchData();
  }, [t]);

  // ---------- Occupation (single-select) ----------
  const selectedOccupationId = state.occupation_id || null;

  const selectOccupation = (id) => {
    updateState({ occupation_id: id });
  };

  // ---------- Hobbies (multi‑select, max 3) ----------
  const selectedHobbyIds = state.hobby_ids || [];

  const toggleHobby = (id) => {
    let nextIds;
    if (selectedHobbyIds.includes(id)) {
      nextIds = selectedHobbyIds.filter((hid) => hid !== id);
    } else {
      if (selectedHobbyIds.length >= MAX_HOBBIES) return;
      nextIds = [...selectedHobbyIds, id];
    }
    updateState({ hobby_ids: nextIds });
  };

  if (isLoading) {
    return (
      <PageCard className="flex flex-col items-center justify-center py-10">
        <Loader2 className="animate-spin text-cyan-400 mb-3" size={32} />
        <p className="text-sm theme-text-muted">{t("wizard.loadingOptions")}</p>
      </PageCard>
    );
  }

  if (error) {
    return (
      <PageCard className="text-center py-8">
        <p className="text-sm text-red-400">{error}</p>
      </PageCard>
    );
  }

  return (
    <PageCard className="space-y-8">
      <div>
        <h3 className="text-lg font-semibold theme-text">
          {t("wizard.step6.title")}
        </h3>
        <p className="mt-1 text-sm theme-text-muted">
          {t("wizard.step6.subtitle")}
        </p>
      </div>

      {/* ---------- OCCUPATION ---------- */}
      <div className="space-y-3">
        <label className="text-sm font-medium theme-text">
          {t("wizard.occupation")}
        </label>

        {occupations.length === 0 ? (
          <p className="text-sm theme-text-muted">
            {t("wizard.noOccupationsAvailable")}
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {occupations.map((occ) => {
              const isSelected = selectedOccupationId === occ.id;
              return (
                <button
                  key={occ.id}
                  type="button"
                  onClick={() => selectOccupation(occ.id)}
                  className={`flex items-start gap-3 rounded-xl border p-4 text-left transition hover:border-cyan-400/50 ${
                    isSelected ? "" : ""
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
                    {occ.name.charAt(0).toUpperCase()}
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
                        {occ.name}
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
                    {occ.description && (
                      <p className="mt-1 text-xs theme-text-muted">
                        {occ.description}
                      </p>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* ---------- HOBBIES ---------- */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium theme-text">
            {t("wizard.hobbies")}
          </label>
          <span
            className="rounded-full px-3 py-1 text-xs font-medium"
            style={{
              backgroundColor: "var(--color-soft)",
              color: "var(--color-text-muted)",
            }}
          >
            {selectedHobbyIds.length} / {MAX_HOBBIES}
          </span>
        </div>

        {hobbies.length === 0 ? (
          <p className="text-sm theme-text-muted">
            {t("wizard.noHobbiesAvailable")}
          </p>
        ) : (
          <>
            {selectedHobbyIds.length >= MAX_HOBBIES && (
              <div className="rounded-xl border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-300">
                {t("wizard.maxHobbiesReached")}
              </div>
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {hobbies.map((hobby) => {
                const isSelected = selectedHobbyIds.includes(hobby.id);
                const isDisabled =
                  !isSelected && selectedHobbyIds.length >= MAX_HOBBIES;

                return (
                  <button
                    key={hobby.id}
                    type="button"
                    onClick={() => toggleHobby(hobby.id)}
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
                      {hobby.name.charAt(0).toUpperCase()}
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
                          {hobby.name}
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
                      {hobby.description && (
                        <p className="mt-1 text-xs theme-text-muted">
                          {hobby.description}
                        </p>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>
    </PageCard>
  );
}
