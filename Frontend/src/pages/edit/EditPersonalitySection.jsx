// src/pages/edit/EditPersonalitySection.jsx

import { useState, useEffect } from "react";
import { useLanguage } from "../../context/LanguageContext";
import { listPersonalities } from "../../services/personalityService";
import {
  TextArea,
  LanguagePills,
  TonePills,
} from "../character/CharacterFormFields";
import { Loader2 } from "lucide-react";

const MAX_PERSONALITIES = 3;

export default function EditPersonalitySection({ form, updateField, errors }) {
  const { t } = useLanguage();

  const [personalities, setPersonalities] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  // Fetch all available personalities
  useEffect(() => {
    async function fetchPersonalities() {
      setIsLoading(true);
      setLoadError(null);
      try {
        const response = await listPersonalities();
        setPersonalities(response?.data?.personalities || []);
      } catch (err) {
        setLoadError(t("characters.personalitiesLoadError"));
      } finally {
        setIsLoading(false);
      }
    }
    fetchPersonalities();
  }, [t]);

  // Current selection (array of IDs) – normalized to strings for comparison
  const selectedIds = (form.personality_ids || []).map(String);

  const togglePersonality = (id) => {
    const idStr = String(id);
    let nextIds;
    if (selectedIds.includes(idStr)) {
      nextIds = selectedIds.filter((pid) => pid !== idStr);
    } else {
      if (selectedIds.length >= MAX_PERSONALITIES) return;
      nextIds = [...selectedIds, idStr];
    }
    updateField("personality_ids", nextIds);
  };

  return (
    <div className="space-y-5 rounded-2xl border p-4 theme-card">
      <h3 className="text-base font-semibold theme-text">
        {t("characters.personalityAndPrompt")}
      </h3>

      {/* ----- Personality Traits (multi‑select) ----- */}
      <div>
        <label className="mb-2 block text-sm font-medium theme-text">
          {t("wizard.personality")} ({t("characters.upTo")} {MAX_PERSONALITIES})
        </label>

        {isLoading ? (
          <div className="flex items-center gap-2 py-4 text-sm theme-text-muted">
            <Loader2 className="animate-spin" size={16} />
            {t("characters.loadingPersonalities")}
          </div>
        ) : loadError ? (
          <p className="text-sm text-red-400">{loadError}</p>
        ) : personalities.length === 0 ? (
          <p className="text-sm theme-text-muted">
            {t("characters.noPersonalitiesAvailable")}
          </p>
        ) : (
          <>
            {selectedIds.length >= MAX_PERSONALITIES && (
              <div className="mb-3 rounded-xl border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-300">
                {t("characters.maxPersonalitiesReached", {
                  max: MAX_PERSONALITIES,
                })}
              </div>
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {personalities.map((pers) => {
                const persIdStr = String(pers.id);
                const isSelected = selectedIds.includes(persIdStr);
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
      </div>

      <TextArea
        label={t("characters.personalityDescription")}
        value={form.personality_description}
        onChange={(e) => updateField("personality_description", e.target.value)}
        rows={6}
        error={errors.personality_description}
        required
      />
      <TextArea
        label={t("characters.greetingMessage")}
        value={form.greeting_message}
        onChange={(e) => updateField("greeting_message", e.target.value)}
        rows={3}
        error={errors.greeting_message}
        required
      />
      <TextArea
        label={t("characters.systemInstructions")}
        value={form.system_instructions}
        onChange={(e) => updateField("system_instructions", e.target.value)}
        rows={4}
      />
      <TextArea
        label={t("characters.promptTemplate")}
        value={form.prompt_template}
        onChange={(e) => updateField("prompt_template", e.target.value)}
        rows={8}
        error={errors.prompt_template}
        required
      />
      <LanguagePills
        value={form.primary_language}
        onChange={(v) => updateField("primary_language", v)}
      />
      <TonePills
        value={form.tone_style}
        onChange={(v) => updateField("tone_style", v)}
      />
    </div>
  );
}
