// src/pages/Dashboard.jsx
import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import PageCard from "../components/PageCard";
import PrimaryButton from "../components/PrimaryButton";
import SectionTitle from "../components/SectionTitle";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import { listConversations } from "../services/conversationService";
import { listCharacters } from "../services/characterService";
import {
  getSparkPoolBalance,
  getCurrentSubscription,
  getMessagesTodayCount,
} from "../services/billingService";
import africanWomanAvatar from "../assets/images/avatars/african-woman.png";
import {
  Loader2,
  MessageCircle,
  UserPlus,
  LogIn,
  Sparkles as Sparks,
  Activity,
  ArrowLeftRight,
} from "lucide-react";

function formatTime(dateStr, t) {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return t("common.justNow");

  if (diffMins < 60) {
    const template = t("common.minAgo");
    return template.replace("{count}", diffMins);
  }

  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) {
    const template = t("common.hAgo");
    return template.replace("{count}", diffHrs);
  }

  const diffDays = Math.floor(diffHrs / 24);
  const template = t("common.dAgo");
  return template.replace("{count}", diffDays);
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { isAuthenticated, user } = useAuth();

  const [conversations, setConversations] = useState([]);
  const [featuredChar, setFeaturedChar] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sparkBalance, setSparkBalance] = useState(null);
  const [subscription, setSubscription] = useState(null);
  const [messagesToday, setMessagesToday] = useState(0);

  const displayName =
    user?.display_name ||
    user?.full_name ||
    user?.username ||
    t("common.guestUser");

  // Fetch all dashboard data
  const fetchDashboardData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [convRes, charRes, balRes, subRes, msgCountRes] = await Promise.all(
        [
          listConversations(),
          listCharacters({ featured_only: true }),
          getSparkPoolBalance(),
          getCurrentSubscription(),
          getMessagesTodayCount(),
        ],
      );
      setConversations(convRes?.data?.conversations || []);
      setFeaturedChar(charRes?.data?.characters?.[0] || null);
      setSparkBalance(balRes?.data?.sparks || 0);
      setSubscription(subRes?.data?.subscription || null);
      setMessagesToday(msgCountRes?.data?.count || 0);
    } catch (err) {
      console.error("Dashboard fetch error:", err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthenticated) {
      setLoading(false);
      return;
    }
    fetchDashboardData();
  }, [isAuthenticated, fetchDashboardData]);

  // ---- Derived stats ----
  const activeConversations = isAuthenticated ? conversations.length : 0;

  const sparkStat = isAuthenticated
    ? sparkBalance !== null
      ? `${sparkBalance.toLocaleString()} ${t("spark_pool.sparks")}`
      : "..."
    : "0";

  const subscriptionStat = isAuthenticated
    ? subscription
      ? subscription.plan_name
      : t("common.free") // assuming "free" exists in common, if not add "common.free": "Free"
    : "-";

  // ---- Recent activities (from real conversations) ----
  const recentActivities = isAuthenticated
    ? conversations.slice(0, 4).map((conv) => ({
        title: conv.character?.name || t("common.chat"),
        subtitle: formatTime(conv.last_message_at, t),
        onClick: () => navigate(`/conversations/${conv.conversation_id}`),
      }))
    : [
        {
          title: t("dashboard.exploreCompanionsGuest"), // new key
          subtitle: t("dashboard.startWithoutAccount"), // new key
          onClick: () => navigate("/characters"),
        },
        {
          title: t("dashboard.createAccountToSave"),
          subtitle: t("dashboard.unlockFeatures"),
          onClick: () => navigate("/register"),
        },
      ];

  // ---- Quick actions ----
  const quickActions = isAuthenticated
    ? [
        {
          label: t("dashboard.exploreCharacters"),
          onClick: () => navigate("/characters"),
          icon: MessageCircle,
        },
        {
          label: t("dashboard.topUpSparkPool"),
          onClick: () => navigate("/spark_pool"),
          icon: Sparks,
        },
        {
          label: t("dashboard.transactions"),
          onClick: () => navigate("/transactions"),
          icon: ArrowLeftRight,
        },
        {
          label: t("dashboard.viewAllChats"),
          onClick: () => navigate("/chats"),
          icon: Activity,
        },
      ]
    : [
        {
          label: t("dashboard.exploreCharacters"),
          onClick: () => navigate("/characters"),
          icon: MessageCircle,
        },
        {
          label: t("common.createAccount"),
          onClick: () => navigate("/register"),
          icon: UserPlus,
        },
        {
          label: t("common.login"),
          onClick: () => navigate("/login"),
          icon: LogIn,
        },
      ];

  // ---- Featured companion ----
  const featured = featuredChar || {
    name: "Amani",
    role: t("dashboard.calmListener"),
    avatar_url: null,
    is_active: true,
  };

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Guest banner */}
      {!isAuthenticated && (
        <section
          className="rounded-3xl border p-4 shadow-sm"
          style={{
            borderColor: "var(--color-accent-soft)",
            backgroundColor: "var(--color-accent-soft)",
          }}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold theme-text">
                  {t("dashboard.guestModeTitle")}
                </h2>
                <StatusBadge status={t("common.guest")} />
              </div>
              <p className="mt-2 text-sm leading-6 theme-text-muted">
                {t("dashboard.guestModeText")}
              </p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <PrimaryButton fullWidth onClick={() => navigate("/register")}>
              {t("common.createAccount")}
            </PrimaryButton>
            <PrimaryButton
              variant="secondary"
              fullWidth
              onClick={() => navigate("/login")}
            >
              {t("common.login")}
            </PrimaryButton>
          </div>
        </section>
      )}

      {/* Hero card */}
      <section className="rounded-3xl border theme-card p-5 shadow-sm">
        <p className="text-sm theme-text-muted">
          {isAuthenticated
            ? t("dashboard.welcomeBack")
            : t("dashboard.welcomeGuest")}
        </p>
        <h2 className="mt-2 text-2xl font-bold tracking-tight theme-text">
          {isAuthenticated
            ? `${t("dashboard.heroTitle")}, ${displayName}`
            : t("dashboard.heroTitleGuest")}
        </h2>
        <p className="mt-3 max-w-sm text-sm leading-6 theme-text-muted">
          {isAuthenticated
            ? t("dashboard.heroText")
            : t("dashboard.heroTextGuest")}
        </p>

        {/* Active subscription highlight */}
        {subscription && (
          <div className="mt-4 rounded-2xl bg-amber-500/10 border border-amber-500/30 p-3 text-sm">
            <span className="font-medium text-amber-600 dark:text-amber-400">
              {subscription.plan_name}
            </span>
            <span className="theme-text-muted">
              {" "}
              {t("dashboard.subscriptionRenews", {
                date: new Date(subscription.ends_at).toLocaleDateString(),
              })}
            </span>
            <div className="mt-1 text-xs theme-text-muted">
              {t("dashboard.sparksRemaining", {
                count: subscription.sparks_remaining?.toLocaleString(),
              })}
            </div>
          </div>
        )}

        {/* Featured companion mini card */}
        <div className="mt-5 flex items-center gap-3 rounded-2xl theme-soft p-3">
          <img
            src={featured.avatar_url || africanWomanAvatar}
            alt={featured.name}
            className="h-14 w-14 rounded-2xl object-cover"
          />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium theme-text">{featured.name}</p>
            <p className="text-xs theme-text-muted">
              {featured.role ||
                featured.short_description ||
                t("dashboard.companionFallback")}
            </p>
          </div>
          <div className="ml-auto">
            <StatusBadge
              status={
                featured.is_active ? t("common.online") : t("common.offline")
              }
            />
          </div>
        </div>
      </section>

      {/* Stats */}
      <section className="space-y-3">
        <SectionTitle>{t("dashboard.overview")}</SectionTitle>
        <div className="grid grid-cols-2 gap-3">
          <StatCard label={t("dashboard.balance")} value={sparkStat} />
          <StatCard
            label={t("dashboard.subscription")}
            value={subscriptionStat}
          />
          <StatCard
            label={t("dashboard.conversations")}
            value={activeConversations}
          />
          <StatCard label={t("dashboard.msgsToday")} value={messagesToday} />
        </div>
      </section>

      {loading && !isAuthenticated ? null : loading ? (
        <div className="flex justify-center py-6">
          <Loader2 className="animate-spin text-sky-400" size={32} />
        </div>
      ) : error ? (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          {error}
        </div>
      ) : null}

      {/* Quick actions */}
      <PageCard>
        <h3 className="text-sm font-semibold theme-text">
          {t("dashboard.quickActions")}
        </h3>
        <div className="mt-3 grid grid-cols-1 gap-3">
          {quickActions.map((action) => (
            <button
              key={action.label}
              onClick={action.onClick}
              className="flex items-center gap-3 rounded-2xl border theme-card px-4 py-3 text-left text-sm font-medium theme-text transition hover:opacity-90"
            >
              <action.icon size={18} className="text-[var(--color-accent)]" />
              {action.label}
            </button>
          ))}
        </div>
      </PageCard>

      {/* Recent activity */}
      <PageCard>
        <SectionTitle
          action={
            <button
              onClick={() => navigate("/chats")}
              className="text-xs font-medium text-[var(--color-accent)]"
            >
              {t("common.viewAll")}
            </button>
          }
        >
          {t("dashboard.recentActivity")}
        </SectionTitle>

        {recentActivities.length === 0 ? (
          <p className="mt-4 text-sm theme-text-muted">
            {isAuthenticated
              ? t("dashboard.startConversation")
              : t("dashboard.signUpOrLogIn")}
          </p>
        ) : (
          <div className="mt-4 space-y-3">
            {recentActivities.map((item) => (
              <button
                key={item.title}
                onClick={item.onClick}
                className="w-full text-left rounded-2xl theme-soft p-3 hover:opacity-90 transition"
              >
                <p className="text-sm theme-text">{item.title}</p>
                <p className="mt-1 text-xs theme-text-muted">{item.subtitle}</p>
              </button>
            ))}
          </div>
        )}
      </PageCard>
    </div>
  );
}
