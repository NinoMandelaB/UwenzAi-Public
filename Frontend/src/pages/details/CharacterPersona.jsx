// src/pages/details/CharacterPersona.jsx

import { Info } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";
import { SectionTitle } from "./shared";

export default function CharacterPersona({
  personalityDescription,
  greetingMessage,
  systemInstructions,
  isAdmin,
}) {
  const { t } = useLanguage();

  if (!personalityDescription && !greetingMessage && !systemInstructions)
    return null;

  // If not admin and the only visible parts are admin‑only, return nothing
  if (!isAdmin && !personalityDescription) return null;

  return (
    <>
      <SectionTitle
        icon={<Info size={18} style={{ color: "var(--color-accent)" }} />}
        title={t("characters.personaDetails")}
      />
      <div
        className="rounded-2xl border p-4 space-y-4"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-card)",
        }}
      >
        {/* Everyone sees personality description */}
        {personalityDescription && (
          <div>
            <h5 className="text-xs font-semibold theme-text-muted uppercase mb-1">
              {t("characters.personalityDescription")}
            </h5>
            <p className="text-sm leading-relaxed theme-text">
              {personalityDescription}
            </p>
          </div>
        )}

        {/* Admin‑only sections */}
        {isAdmin && greetingMessage && (
          <div>
            <h5 className="text-xs font-semibold theme-text-muted uppercase mb-1">
              {t("characters.greetingMessage")}
            </h5>
            <p className="text-sm leading-relaxed theme-text">
              {greetingMessage}
            </p>
          </div>
        )}

        {isAdmin && systemInstructions && (
          <div>
            <h5 className="text-xs font-semibold theme-text-muted uppercase mb-1">
              {t("characters.systemInstructions")}
            </h5>
            <p className="text-sm leading-relaxed theme-text">
              {systemInstructions}
            </p>
          </div>
        )}
      </div>
    </>
  );
}
