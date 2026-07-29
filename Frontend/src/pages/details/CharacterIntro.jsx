import { Lock } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";

export default function CharacterIntro({ intro, isPublic }) {
  const { t } = useLanguage();

  if (!intro && isPublic) return null;

  return (
    <>
      {intro && (
        <div
          className="rounded-2xl border p-4"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-soft)",
          }}
        >
          <p className="text-xs font-bold uppercase tracking-widest theme-text-muted mb-2">
            {t("characters.introduction")}
          </p>
          <p className="text-sm leading-relaxed theme-text">{intro}</p>
        </div>
      )}

      {!isPublic && (
        <div className="mt-4 flex justify-center sm:justify-start">
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-300">
            <Lock size={12} />
            {t("characters.privateVisibleOnlyToYou")}
          </span>
        </div>
      )}
    </>
  );
}
