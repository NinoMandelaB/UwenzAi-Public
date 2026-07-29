// src/components/CharacterInactiveBanner.jsx

import { useLanguage } from "../context/LanguageContext";
import { useTheme } from "../context/ThemeContext"; // import your theme hook

export default function CharacterInactiveBanner() {
  const { t } = useLanguage();
  const { isDark } = useTheme();

  return (
    <div className="mx-4 mb-2 rounded-xl border border-amber-400/30 bg-amber-500/10 p-3 text-center">
      <p className={`text-sm ${isDark ? "text-amber-300" : "text-black/50"}`}>
        {t("common.characterInactiveMessage")}
      </p>
    </div>
  );
}
