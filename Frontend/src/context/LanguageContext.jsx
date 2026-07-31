import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { translations } from "../i18n/translations";

const LanguageContext = createContext(null);

export function LanguageProvider({ children }) {
  const [language, setLanguage] = useState(() => {
    return localStorage.getItem("eaaic-language") || "en";
  });

  useEffect(() => {
    localStorage.setItem("eaaic-language", language);
  }, [language]);

  function t(path, vars = {}) {
    const keys = path.split(".");
    let value = translations[language];

    for (const key of keys) {
      value = value?.[key];
    }

    let result = value || path;
    Object.entries(vars).forEach(([k, v]) => {
      result = result.replace(`{${k}}`, v);
    });
    return result;
  }

  const value = useMemo(
    () => ({
      language,
      setLanguage,
      t,
      isEnglish: language === "en",
      isSwahili: language === "sw",
    }),
    [language],
  );

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  const context = useContext(LanguageContext);

  if (!context) {
    throw new Error("useLanguage must be used inside LanguageProvider");
  }

  return context;
}
