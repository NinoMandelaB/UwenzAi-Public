import { Sparkles } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";
import { SectionTitle } from "./shared";

function Tag({ children }) {
  return (
    <span
      className="inline-block rounded-full border px-3 py-1 text-xs font-medium theme-text"
      style={{ borderColor: "var(--color-border)" }}
    >
      {children}
    </span>
  );
}

export default function CharacterPersonality({
  personalityIds,
  personalityMap,
}) {
  const { t } = useLanguage();
  const hasPersonalities = personalityIds && personalityIds.length > 0;

  return (
    <>
      <SectionTitle
        icon={<Sparkles size={18} style={{ color: "var(--color-accent)" }} />}
        title={t("characters.personality")}
      />
      <div
        className="rounded-2xl border p-4 space-y-3"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-card)",
        }}
      >
        {hasPersonalities ? (
          <div className="flex flex-wrap gap-2">
            {personalityIds.map((id) => (
              <Tag key={id}>{personalityMap[id] || id}</Tag>
            ))}
          </div>
        ) : (
          <p className="text-sm theme-text-muted">
            {t("characters.noPersonalitySelected")}
          </p>
        )}
      </div>
    </>
  );
}
