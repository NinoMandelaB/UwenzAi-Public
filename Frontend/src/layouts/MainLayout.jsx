import { Outlet, NavLink } from "react-router-dom";
import {
  House,
  Users,
  MessageCircle,
  Sparkles as SparkPool,
  Settings,
} from "lucide-react";
import AppHeader from "../components/AppHeader";
import BottomNav from "../components/BottomNav";
import { useLanguage } from "../context/LanguageContext";
import { NAV_ITEMS } from "../utils/navItems";
import NavUnreadBadge from "../components/NavUnreadBadge";

export default function MainLayout() {
  const { t } = useLanguage();
  const navItems = NAV_ITEMS(t);

  return (
    <div className="min-h-screen theme-page flex flex-col">
      {/* ---- TOP APPBAR – always visible, full width ---- */}
      <AppHeader />

      {/* ---- BELOW HEADER: sidebar + main content ---- */}
      <div className="flex flex-1 overflow-hidden">
        {/* Desktop Sidebar */}
        <aside className="hidden lg:flex flex-col w-64 border-r theme-card-soft p-4">
          {/* Navigation links */}
          <nav className="flex-1 space-y-1">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/dashboard" || item.to === "/chats"}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium transition ${
                    isActive
                      ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                      : "hover:bg-black/5 dark:hover:bg-white/15"
                  }`
                }
              >
                <span className="relative">
                  <item.icon size={20} />
                  {item.to === "/chats" && <NavUnreadBadge />}
                </span>
                <span className="flex items-center gap-2">
                  {item.label}
                  {item.badge && (
                    <span className="px-2 py-0.5 text-xs bg-amber-500/20 text-amber-600 dark:bg-amber-500/30 dark:text-amber-400 rounded-full">
                      {item.badge}
                    </span>
                  )}
                </span>
              </NavLink>
            ))}
          </nav>
        </aside>

        {/* Main scrollable content */}
        <main className="min-w-0 flex-1 overflow-y-auto px-4 pb-24 lg:pb-6 pt-5">
          <div className="min-w-0 w-full max-w-md mx-auto lg:max-w-none lg:mx-0">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Mobile bottom nav – hidden on desktop */}
      <BottomNav className="lg:hidden" />
    </div>
  );
}
