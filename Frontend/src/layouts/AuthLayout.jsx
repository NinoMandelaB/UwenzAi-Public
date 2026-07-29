// src/layouts/AuthLayout.jsx

import { Link, Outlet } from "react-router-dom";
import { useLanguage } from "../context/LanguageContext";
import { useTheme } from "../context/ThemeContext";
import { useAuth } from "../context/AuthContext"; // <-- NEW
import { Moon, Sun } from "lucide-react";

export default function AuthLayout() {
  const { language, setLanguage, t } = useLanguage();
  const { isDark, toggleTheme } = useTheme();
  const { isAuthenticated } = useAuth(); // <-- NEW

  return (
    <div className="min-h-screen theme-page">
      {/* ── Responsive container – wider on desktop ── */}
      <div className="mx-auto flex min-h-screen max-w-md lg:max-w-2xl flex-col justify-center px-4 py-8">
        {/* Language + Theme pill (unchanged) */}
        <div className="mb-4 flex justify-center">
          <div
            className="inline-flex items-center gap-2 rounded-full border p-1"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-soft)",
            }}
          >
            <button
              type="button"
              onClick={() => setLanguage("en")}
              className="rounded-full px-3 py-1.5 text-xs font-medium transition"
              style={{
                backgroundColor:
                  language === "en" ? "var(--color-accent)" : "transparent",
                color:
                  language === "en" ? "#082f49" : "var(--color-text-muted)",
              }}
            >
              EN
            </button>
            <button
              type="button"
              onClick={() => setLanguage("sw")}
              className="rounded-full px-3 py-1.5 text-xs font-medium transition"
              style={{
                backgroundColor:
                  language === "sw" ? "var(--color-accent)" : "transparent",
                color:
                  language === "sw" ? "#082f49" : "var(--color-text-muted)",
              }}
            >
              SW
            </button>
            <div
              className="h-5 w-px"
              style={{ backgroundColor: "var(--color-border)" }}
            />
            <button
              type="button"
              onClick={toggleTheme}
              className="rounded-full px-3 py-1.5 text-xs font-medium transition hover:bg-black/5 dark:hover:bg-white/10"
              aria-label="Toggle theme"
              style={{ color: "var(--color-text-muted)" }}
            >
              {isDark ? (
                <Sun size={18} className="text-amber-400" />
              ) : (
                <Moon size={18} className="text-indigo-600" />
              )}
            </button>
          </div>
        </div>

        {/* Logo */}
        <Link to="/" className="inline-block text-center">
          <h1 className="text-2xl lg:text-3xl font-bold tracking-tight theme-text">
            UwenzAi
          </h1>
        </Link>

        {/* Subtitle */}
        <p className="mt-2 text-center text-sm lg:text-base theme-text-muted">
          {t("common.exploringEaaic")}
        </p>

        {/* Guest entry – only show if NOT authenticated */}
        {!isAuthenticated && (
          <Link
            to="/"
            className="mt-3 text-center text-sm font-medium transition hover:opacity-80"
            style={{ color: "var(--color-accent)" }}
          >
            {t("common.continueAsGuest")}
          </Link>
        )}

        {/* Auth card – wider and more padding on desktop */}
        <div className="mt-6 rounded-3xl border theme-card-soft p-5 lg:p-8 shadow-2xl backdrop-blur">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
