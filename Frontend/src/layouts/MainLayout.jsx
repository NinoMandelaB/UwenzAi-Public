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
import FeedbackButton from "../components/FeedbackButton";
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
                <span>{item.label}</span>
              </NavLink>
            ))}
          </nav>

          {/* Feedback button at bottom */}
          <div className="mt-auto pt-4 border-t border-[var(--color-border)]">
            <FeedbackButton />
          </div>
        </aside>

        {/* Main scrollable content */}
        <main className="flex-1 overflow-y-auto px-4 pb-24 lg:pb-6 pt-5">
          <div className="w-full max-w-md mx-auto lg:max-w-none lg:mx-0">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Mobile bottom nav – hidden on desktop */}
      <BottomNav className="lg:hidden" />
    </div>
  );
}
