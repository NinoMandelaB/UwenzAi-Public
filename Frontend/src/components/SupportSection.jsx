// src/components/SupportSection.jsx

import { useNavigate } from "react-router-dom";
import { Info, UserRound, ChevronRight } from "lucide-react";
import SectionTitle from "./SectionTitle";

function SettingsItem({ title, subtitle, icon: Icon, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full rounded-2xl border p-4 text-left transition hover:opacity-95 theme-card"
    >
      <div className="flex items-center gap-3">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border"
          style={{
            backgroundColor: "var(--color-soft)",
            borderColor: "var(--color-border)",
            color: "var(--color-accent)",
          }}
        >
          <Icon size={18} />
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold theme-text">{title}</p>
          <p className="mt-1 text-sm leading-6 theme-text-muted">{subtitle}</p>
        </div>

        <div className="shrink-0 theme-text-muted">
          <ChevronRight size={18} />
        </div>
      </div>
    </button>
  );
}

export default function SupportSection({ currentLanguage, currentTheme }) {
  const navigate = useNavigate();

  return (
    <section className="space-y-3">
      <SectionTitle>Support</SectionTitle>

      <div className="space-y-3">
        <SettingsItem
          title="Help Center"
          subtitle="Contact info, and feedback"
          icon={Info}
          onClick={() => navigate("/contact")}
        />

        <SettingsItem
          title="About UwenzAi"
          subtitle={`Language: ${currentLanguage} • Theme: ${currentTheme}`}
          icon={UserRound}
          onClick={() => {}}
        />
      </div>
    </section>
  );
}
