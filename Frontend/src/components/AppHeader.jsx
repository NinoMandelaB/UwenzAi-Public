// src/components/AppHeader.jsx
import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Loader2,
  Moon,
  Sun,
  User as UserIcon,
  Sparkles,
  LogOut,
  LogIn,
  UserPlus,
  User,
  ArrowLeftRight,
  AlertTriangle,
  Ticket,
  Mail,
} from "lucide-react";
import { useTheme } from "../context/ThemeContext";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useSparkPoolBalance } from "../hooks/useSparkPoolBalance";
import { getUnreadCount } from "../services/supportService";

export default function AppHeader({ compact = false, className = "" }) {
  const navigate = useNavigate();
  const { isDark, toggleTheme } = useTheme();
  const { t } = useLanguage();
  const { user, isAuthenticated, logout, isLoading: isAuthLoading } = useAuth();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [showAvatarMenu, setShowAvatarMenu] = useState(false);
  const menuRef = useRef(null);
  const avatarMenuRef = useRef(null);

  const [unreadSupportCount, setUnreadSupportCount] = useState(0);

  // Use the SAME hook as ChatModal – this works for guests and auth
  const { balance: sparkBalance } = useSparkPoolBalance();

  const displayName = isAuthenticated
    ? user?.display_name ||
      user?.full_name ||
      user?.username ||
      t("common.user")
    : t("common.guest");

  const formatSparkBalance = () => {
    if (sparkBalance === null) return "...";
    return `${sparkBalance.toLocaleString()} ${t("spark_pool.sparks")}`;
  };

  // ── Support unread count ──
  const fetchUnreadCount = async () => {
    if (!isAuthenticated) return;
    try {
      const res = await getUnreadCount();
      if (res.status === "success") {
        setUnreadSupportCount(res.data.count || 0);
      }
    } catch (err) {
      console.warn("Failed to fetch unread support count:", err);
    }
  };

  useEffect(() => {
    if (!isAuthenticated) {
      setUnreadSupportCount(0);
      return;
    }
    fetchUnreadCount();
    const interval = setInterval(fetchUnreadCount, 30000);
    return () => clearInterval(interval);
  }, [isAuthenticated]);

  useEffect(() => {
    const handleUnreadUpdate = () => {
      if (isAuthenticated) fetchUnreadCount();
    };
    window.addEventListener("support-unread-updated", handleUnreadUpdate);
    return () =>
      window.removeEventListener("support-unread-updated", handleUnreadUpdate);
  }, [isAuthenticated]);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target))
        setShowMenu(false);
      if (avatarMenuRef.current && !avatarMenuRef.current.contains(e.target))
        setShowAvatarMenu(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleNavigate = (path) => {
    setShowMenu(false);
    setShowAvatarMenu(false);
    navigate(path);
  };

  const handleLogout = async () => {
    if (isLoggingOut) return;
    try {
      setIsLoggingOut(true);
      setShowMenu(false);
      setShowAvatarMenu(false);
      await logout();
      navigate("/login");
    } finally {
      setIsLoggingOut(false);
    }
  };

  if (compact) {
    return (
      <div className={`flex flex-col items-center gap-4 ${className}`}>...</div>
    );
  }

  return (
    <header
      className={`sticky top-0 z-50 w-full transition-colors duration-300 ${className}`}
      style={{
        backgroundColor: isDark ? "#111827" : "#ffffff",
        boxShadow: isDark
          ? "0 10px 30px -10px rgba(0, 0, 0, 0.7)"
          : "0 4px 20px -5px rgba(0, 0, 0, 0.05)",
      }}
    >
      <div className="mx-auto flex items-center justify-between px-4 py-4 max-w-md lg:max-w-none">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-tr from-indigo-600 to-violet-500 text-white shadow-lg">
            <Sparkles size={24} fill="currentColor" />
          </div>
          <div className="flex flex-col">
            <h1 className="theme-text mb-1 text-[16px] leading-none font-black tracking-tight">
              {t("common.alphaWarning")}
            </h1>
            <div className="flex items-center gap-2">
              <span className="theme-text text-[13px] font-bold opacity-70">
                {displayName}
              </span>
              <div className="h-1 w-1 rounded-full bg-slate-400 opacity-40" />
              <div className="flex items-center gap-1 text-[12px] font-black text-amber-500">
                <Sparkles size={14} strokeWidth={3} />
                {formatSparkBalance()}
              </div>
            </div>
          </div>
        </div>

        <div className="relative flex items-center gap-2" ref={menuRef}>
          {isAuthenticated && (
            <button
              onClick={() => handleNavigate("/support")}
              className="relative rounded-full p-2.5 transition-colors hover:bg-slate-50 dark:hover:bg-white/5"
              aria-label={t("common.support")}
              title={t("common.support")}
            >
              <Ticket size={20} className="theme-text opacity-70" />
              <span
                className={`absolute -top-0.5 -right-0.5 flex h-5 min-w-[20px] items-center justify-center rounded-full px-1 text-[10px] font-bold text-white transition-colors ${
                  unreadSupportCount > 0
                    ? "bg-red-500"
                    : "bg-gray-400 dark:bg-gray-600"
                }`}
                style={{ lineHeight: 1 }}
              >
                {unreadSupportCount > 99 ? "99+" : unreadSupportCount}
              </span>
            </button>
          )}

          <button
            onClick={toggleTheme}
            className="rounded-full p-2.5 transition-colors hover:bg-slate-50 dark:hover:bg-white/5"
          >
            {isDark ? (
              <Sun size={20} className="text-amber-400" />
            ) : (
              <Moon size={20} className="text-indigo-600" />
            )}
          </button>

          <div className="relative" ref={avatarMenuRef}>
            <button
              onClick={() => {
                if (isAuthenticated) setShowAvatarMenu((prev) => !prev);
                else setShowMenu(!showMenu);
              }}
              className="relative flex h-11 w-11 items-center justify-center rounded-full shadow-md transition-all active:scale-95"
              style={{
                backgroundColor: isDark
                  ? "rgba(255,255,255,0.12)"
                  : "rgba(0,0,0,0.06)",
              }}
            >
              {isLoggingOut ? (
                <Loader2 size={18} className="theme-text animate-spin" />
              ) : isAuthenticated ? (
                <div className="theme-text text-[13px] font-black uppercase tracking-tighter">
                  {user?.username?.slice(0, 2) || "U"}
                </div>
              ) : (
                <UserIcon size={20} className="theme-text opacity-70" />
              )}
              <span
                className="absolute bottom-0 left-0 h-3.5 w-3.5 rounded-full border-[2.5px] bg-emerald-500 shadow-sm"
                style={{ borderColor: isDark ? "#111827" : "#ffffff" }}
              />
            </button>

            {showAvatarMenu && isAuthenticated && (
              <div
                className="animate-in fade-in slide-in-from-top-2 absolute top-14 right-0 min-w-[180px] overflow-hidden rounded-2xl duration-200 z-50"
                style={{
                  backgroundColor: isDark ? "#1f2937" : "#ffffff",
                  boxShadow: "0 20px 50px -10px rgba(0, 0, 0, 0.4)",
                }}
              >
                <div className="flex flex-col p-2">
                  <button
                    onClick={() => handleNavigate("/profile")}
                    className="theme-text flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-[14px] font-bold whitespace-nowrap transition-all hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-indigo-400/10 dark:hover:text-indigo-400"
                  >
                    <User size={18} className="opacity-70" />
                    <span>{t("common.profile")}</span>
                  </button>
                  <button
                    onClick={() => handleNavigate("/transactions")}
                    className="theme-text flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-[14px] font-bold whitespace-nowrap transition-all hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-indigo-400/10 dark:hover:text-indigo-400"
                  >
                    <ArrowLeftRight size={18} className="opacity-70" />
                    <span>{t("common.transactions")}</span>
                  </button>
                  <button
                    onClick={() => handleNavigate("/contact")}
                    className="theme-text flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-[14px] font-bold whitespace-nowrap transition-all hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-indigo-400/10 dark:hover:text-indigo-400"
                  >
                    <Mail size={18} className="opacity-70" />
                    <span>{t("common.contactFeedback")}</span>
                  </button>
                  <button
                    onClick={handleLogout}
                    disabled={isLoggingOut}
                    className="flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-[14px] font-bold whitespace-nowrap text-red-500 transition-all hover:bg-red-500/10 disabled:opacity-60"
                  >
                    {isLoggingOut ? (
                      <Loader2 size={18} className="animate-spin" />
                    ) : (
                      <LogOut size={18} />
                    )}
                    <span>
                      {isLoggingOut
                        ? t("common.signingOut")
                        : t("common.signOut")}
                    </span>
                  </button>
                </div>
              </div>
            )}

            {showMenu && !isAuthenticated && (
              <div
                className="animate-in fade-in slide-in-from-top-2 absolute top-14 right-0 min-w-[240px] overflow-hidden rounded-2xl duration-200 z-50"
                style={{
                  backgroundColor: isDark ? "#1f2937" : "#ffffff",
                  boxShadow: "0 20px 50px -10px rgba(0, 0, 0, 0.4)",
                }}
              >
                <div className="flex flex-col p-2">
                  <button
                    onClick={() => handleNavigate("/login")}
                    className="theme-text flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-[14px] font-bold whitespace-nowrap transition-all hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-indigo-400/10 dark:hover:text-indigo-400"
                  >
                    <LogIn size={18} className="opacity-70" />
                    <span>{t("common.signIn")}</span>
                  </button>
                  <button
                    onClick={() => handleNavigate("/register")}
                    className="theme-text flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-[14px] font-bold whitespace-nowrap transition-all hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-indigo-400/10 dark:hover:text-indigo-400"
                  >
                    <UserPlus size={18} className="opacity-70" />
                    <span>{t("common.signUp")}</span>
                  </button>
                  <button
                    onClick={() => handleNavigate("/contact")}
                    className="theme-text flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-[14px] font-bold whitespace-nowrap transition-all hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:bg-indigo-400/10 dark:hover:text-indigo-400"
                  >
                    <Mail size={18} className="opacity-70" />
                    <span>{t("common.contactFeedback")}</span>
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <div
        className="flex items-center justify-center sm:justify-start gap-2 px-4 py-1.5 text-center sm:text-left text-xs font-medium border-t"
        style={{
          borderColor: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.05)",
          color: isDark ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.5)",
        }}
      >
        <AlertTriangle size={14} className="text-amber-400 flex-shrink-0" />
        <span>
          {t("common.adminWarning") ||
            "AI-Generated Content. Content may be offensive and incorrect"}
        </span>
      </div>
    </header>
  );
}
