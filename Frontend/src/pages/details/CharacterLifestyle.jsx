import { Briefcase } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";
import { SectionTitle, AttributeRow } from "./shared";

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

export default function CharacterLifestyle({ occupation, hobbyIds, hobbyMap }) {
  const { t } = useLanguage();
  const hasHobbies = hobbyIds && hobbyIds.length > 0;

  return (
    <>
      <SectionTitle
        icon={<Briefcase size={18} style={{ color: "var(--color-accent)" }} />}
        title={t("characters.lifestyle")}
      />
      <div
        className="rounded-2xl border p-4 space-y-3"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-card)",
        }}
      >
        <AttributeRow
          label={t("wizard.occupation")}
          value={occupation?.name || "—"}
        />

        <div>
          <h5 className="text-xs font-semibold theme-text-muted uppercase mb-2">
            {t("wizard.hobbies")}
          </h5>
          {hasHobbies ? (
            <div className="flex flex-wrap gap-2">
              {hobbyIds.map((id) => (
                <Tag key={id}>{hobbyMap[id] || id}</Tag>
              ))}
            </div>
          ) : (
            <p className="text-sm theme-text-muted">
              {t("characters.noHobbiesSelected")}
            </p>
          )}
        </div>
      </div>
    </>
  );
}
