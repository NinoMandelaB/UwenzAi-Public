// src/components/BottomNav.jsx

import { NavLink } from "react-router-dom";
import { useLanguage } from "../context/LanguageContext";
import { NAV_ITEMS } from "../utils/navItems";

export default function BottomNav() {
  const { t } = useLanguage();
  const navItems = NAV_ITEMS(t);

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-40 border-t backdrop-blur lg:hidden"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-card-soft)",
      }}
    >
      <div className="mx-auto flex h-[68px] max-w-md items-center justify-around px-2 py-2">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/dashboard" || item.to === "/chats"}
              className={({ isActive }) =>
                `flex min-w-0 flex-col items-center justify-center gap-1 rounded-2xl px-3 py-2 text-[11px] transition hover:opacity-90 ${
                  isActive ? "font-medium" : ""
                }`
              }
              style={({ isActive }) => ({
                backgroundColor: isActive
                  ? "var(--color-accent-soft)"
                  : "transparent",
                color: isActive
                  ? "var(--color-accent)"
                  : "var(--color-text-muted)",
              })}
            >
              <Icon size={19} strokeWidth={2.2} />
              <span className="truncate">{item.label}</span>
            </NavLink>
          );
        })}
      </div>
    </nav>
  );
}
