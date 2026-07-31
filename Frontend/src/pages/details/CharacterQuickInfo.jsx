// src/pages/details/CharacterQuickInfo.jsx

import { Flame, Sparkles, Globe, Shield } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";

function InfoCard({ icon: Icon, label, value }) {
  return (
    <div
      className="rounded-2xl border p-3 flex items-center gap-3"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-soft)",
      }}
    >
      <Icon size={18} style={{ color: "var(--color-accent)" }} />
      <div>
        <p className="text-xs font-medium uppercase theme-text-muted">
          {label}
        </p>
        <p className="text-sm font-medium theme-text">{value || "—"}</p>
      </div>
    </div>
  );
}

export default function CharacterQuickInfo({
  mood,
  category,
  language,
  status,
}) {
  const { t } = useLanguage();

  return (
    <div className="grid gap-3 sm:grid-cols-4">
      {mood && (
        <InfoCard icon={Flame} label={t("characters.mood")} value={mood} />
      )}
      <InfoCard
        icon={Sparkles}
        label={t("characters.category")}
        value={category}
      />
      <InfoCard
        icon={Globe}
        label={t("characters.language")}
        value={language}
      />
      <InfoCard
        icon={Shield}
        label={t("characters.status")}
        value={status?.replace(/_/g, " ").toUpperCase()}
      />
    </div>
  );
}
