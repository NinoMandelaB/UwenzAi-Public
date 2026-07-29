// src/pages/SparkPool.jsx
import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import PageCard from "../components/PageCard";
import PrimaryButton from "../components/PrimaryButton";
import SectionTitle from "../components/SectionTitle";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import {
  listPackages,
  initiatePayment,
  getSparkPoolBalance,
  getSparkTransactions,
  getCurrentSubscription,
} from "../services/billingService";
import { ensureGuestSession } from "../utils/session";
import { apiFetch } from "../services/apiClient";
import { API } from "../config/constants";
import SparkPackageCard from "../components/Billing/SparkPackageCard";
import SubscriptionPlanCard from "../components/Billing/SubscriptionPlanCard";

function formatRelativeTime(isoString, t) {
  if (!isoString) return "";
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now - date;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return t("common.justNow");
  if (mins < 60) {
    const template = t("common.minAgo");
    return template.replace("{count}", mins);
  }
  const hours = Math.floor(mins / 60);
  if (hours < 24) {
    const template = t("common.hrAgo");
    return template.replace("{count}", hours);
  }
  return date.toLocaleDateString();
}

function formatDate(isoString) {
  if (!isoString) return "";
  return new Date(isoString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

const BILLING_CYCLES = ["daily", "weekly", "monthly", "yearly"];

export default function SparkPool() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { isAuthenticated, user } = useAuth();

  const [selectedSlug, setSelectedSlug] = useState(null);
  const [guestBalance, setGuestBalance] = useState(() => {
    const cached = localStorage.getItem("sparks_cache");
    return cached ? parseInt(cached, 10) : 0;
  });
  const [packages, setPackages] = useState([]);
  const [balance, setBalance] = useState(0);
  const [transactions, setTransactions] = useState([]);
  const [activeSubscription, setActiveSubscription] = useState(null);
  const [loadingPackages, setLoadingPackages] = useState(true);
  const [loadingPayment, setLoadingPayment] = useState(null);
  const [confirmDialog, setConfirmDialog] = useState(null);

  // Tab state for subscription plans
  const [activeCycle, setActiveCycle] = useState("daily");

  const fetchPackages = useCallback(async () => {
    setLoadingPackages(true);
    try {
      const res = await listPackages();
      const all = res.data.packages || [];
      setPackages(all);
      if (all.length > 0) {
        setSelectedSlug(all[0].slug);
      }
    } catch (err) {
      console.error("Failed to load packages", err);
    } finally {
      setLoadingPackages(false);
    }
  }, []);

  const fetchBalance = useCallback(async () => {
    try {
      const data = await getSparkPoolBalance();
      setBalance(data.data.sparks);
    } catch (err) {
      console.error("Failed to load balance", err);
    }
  }, []);

  const fetchTransactions = useCallback(async () => {
    try {
      const data = await getSparkTransactions();
      setTransactions(data.data.transactions || []);
    } catch (err) {
      console.error("Failed to load transactions", err);
    }
  }, []);

  const fetchSubscription = useCallback(async () => {
    try {
      const data = await getCurrentSubscription();
      setActiveSubscription(data.data.subscription);
    } catch (err) {
      console.error("Failed to load subscription", err);
    }
  }, []);

  // Fetch guest balance from backend on mount
  useEffect(() => {
    if (!isAuthenticated) {
      ensureGuestSession()
        .then(() =>
          apiFetch(API.SPARK_POOL.BALANCE, {
            method: "GET",
            skipCsrf: true,
            skipAuthHeader: true,
          }),
        )
        .then((r) => r.json())
        .then((data) => {
          if (data.status === "success") {
            setGuestBalance(data.data.sparks);
            localStorage.setItem("sparks_cache", String(data.data.sparks));
          }
        })
        .catch(() => {});
    }
  }, [isAuthenticated]);

  useEffect(() => {
    fetchPackages();
    if (isAuthenticated) {
      fetchBalance();
      fetchTransactions();
      fetchSubscription();
    }
  }, [
    isAuthenticated,
    fetchPackages,
    fetchBalance,
    fetchTransactions,
    fetchSubscription,
  ]);

  const handlePurchaseConfirm = async () => {
    if (!confirmDialog) return;
    const packageSlug = confirmDialog.slug;
    const packageType = confirmDialog.type;
    setLoadingPayment(packageSlug);
    setConfirmDialog(null);
    try {
      if (!isAuthenticated) {
        await ensureGuestSession();
      }
      // No return URL is sent: the server owns the redirect target and builds it
      // from its own allowlist, so a tampered client cannot steer the return
      // anywhere. PaymentVerify still proves ownership with the guest cookie.
      const result = await initiatePayment({
        package_slug: packageSlug,
        package_type: packageType,
        isGuest: !isAuthenticated,
      });
      window.location.href = result.data.authorization_url;
    } catch (err) {
      console.error("Payment initiation failed", err);
    } finally {
      setLoadingPayment(null);
    }
  };

  const sparkPackages = packages.filter((p) => p.type === "spark");
  const subscriptionPlans = packages.filter((p) => p.type === "subscription");

  // Group subscription plans by cycle
  const plansByCycle = BILLING_CYCLES.reduce((acc, cycle) => {
    acc[cycle] = subscriptionPlans.filter((p) => p.billing_cycle === cycle);
    return acc;
  }, {});

  return (
    <div className="space-y-6 max-w-2xl mx-auto lg:mx-0">
      {/* Guest Notice */}
      {!isAuthenticated && (
        <section
          className="rounded-3xl border p-4 shadow-sm"
          style={{
            borderColor:
              "color-mix(in srgb, var(--color-warning) 25%, transparent)",
            backgroundColor:
              "color-mix(in srgb, var(--color-warning) 10%, transparent)",
          }}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold theme-text">
                  {t("spark_pool.guestSparkPoolAccess")}
                </h2>
                <StatusBadge status={t("common.guest")} />
              </div>
              <p className="mt-2 text-sm theme-text-muted">
                {t("spark_pool.guestSparkPoolText")}
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

      {/* Balance */}
      <section className="rounded-3xl border theme-card p-5 shadow-xl">
        <p className="text-sm theme-text-muted">
          {t("spark_pool.availableBalance")}
        </p>
        <h2 className="mt-2 text-3xl font-bold tracking-tight theme-text">
          {(isAuthenticated ? balance : guestBalance).toLocaleString()}{" "}
          {t("spark_pool.sparks")}
        </h2>
        <p className="mt-3 text-sm leading-6 theme-text-muted">
          {t("spark_pool.balanceHelpText")}
        </p>
      </section>

      {/* Active Subscription (if any) */}
      {activeSubscription && (
        <PageCard>
          <SectionTitle>{t("spark_pool.currentSubscription")}</SectionTitle>
          <div className="mt-2 space-y-2 text-sm">
            <p className="theme-text font-medium">
              {activeSubscription.plan_name}
            </p>
            <p className="theme-text-muted">
              {activeSubscription.billing_cycle} • KES{" "}
              {activeSubscription.price} • {t("spark_pool.renews")}{" "}
              {formatDate(activeSubscription.ends_at)}
            </p>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-2">
              <span className="theme-text-muted">
                {t("spark_pool.sparksIncluded")}
              </span>
              <span className="text-right theme-text">
                {activeSubscription.included_sparks}
              </span>
              <span className="theme-text-muted">
                {t("spark_pool.sparksRemaining")}
              </span>
              <span className="text-right theme-text font-medium">
                {activeSubscription.sparks_remaining?.toLocaleString()}
              </span>
              <span className="theme-text-muted">
                {t("spark_pool.messagePairs")}
              </span>
              <span className="text-right theme-text">
                {activeSubscription.included_message_pairs ?? "∞"}
              </span>
              <span className="theme-text-muted">
                {t("spark_pool.characters")}
              </span>
              <span className="text-right theme-text">
                {activeSubscription.included_characters ?? "∞"}
              </span>
              <span className="theme-text-muted">{t("spark_pool.images")}</span>
              <span className="text-right theme-text">
                {activeSubscription.included_images ?? "∞"}
              </span>
            </div>
            {activeSubscription.usage && (
              <div
                className="mt-3 pt-3 border-t"
                style={{ borderColor: "var(--color-border)" }}
              >
                <p className="text-xs font-medium theme-text-muted mb-2">
                  {t("spark_pool.currentCycleUsage")}
                </p>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <span className="theme-text-muted">
                      {t("spark_pool.msgs")}
                    </span>{" "}
                    <span className="theme-text">
                      {activeSubscription.usage.message_pairs_used}
                    </span>
                  </div>
                  <div>
                    <span className="theme-text-muted">
                      {t("spark_pool.chars")}
                    </span>{" "}
                    <span className="theme-text">
                      {activeSubscription.usage.characters_created}
                    </span>
                  </div>
                  <div>
                    <span className="theme-text-muted">
                      {t("spark_pool.imgs")}
                    </span>{" "}
                    <span className="theme-text">
                      {activeSubscription.usage.images_generated}
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>
        </PageCard>
      )}

      {/* Guest purchase risk warning */}
      {!isAuthenticated && (
        <section
          className="rounded-3xl border p-4 shadow-sm"
          style={{
            borderColor:
              "color-mix(in srgb, var(--color-danger, #ef4444) 25%, transparent)",
            backgroundColor:
              "color-mix(in srgb, var(--color-danger, #ef4444) 8%, transparent)",
          }}
        >
          <p className="text-sm font-semibold theme-text mb-1">
            {t("spark_pool.guestPurchaseRiskTitle")}
          </p>
          <p className="text-sm theme-text-muted leading-6">
            {t("spark_pool.guestPurchaseRiskTextBefore")}
            <button
              onClick={() => navigate("/register")}
              className="underline font-medium"
              style={{ color: "var(--color-accent)" }}
            >
              {t("common.createAccount")}
            </button>
            {t("spark_pool.guestPurchaseRiskTextAfter")}
          </p>
        </section>
      )}

      {/* Top-up Packages (Spark packages) */}
      <PageCard>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold theme-text">
              {t("spark_pool.topUpPackages")}
            </h3>
            <p className="mt-1 text-sm theme-text-muted">
              {t("spark_pool.packageHelpText")}
            </p>
          </div>
        </div>

        {loadingPackages ? (
          <div className="py-8 text-center text-sm theme-text-muted">
            {t("spark_pool.loadingPackages")}
          </div>
        ) : sparkPackages.length === 0 ? (
          <div className="py-8 text-center text-sm theme-text-muted">
            {t("spark_pool.noPackagesAvailable")}
          </div>
        ) : (
          <div className="mt-4 space-y-3">
            {sparkPackages.map((pkg) => (
              <SparkPackageCard
                key={pkg.slug}
                pkg={pkg}
                isSelected={pkg.slug === selectedSlug}
                onSelect={() => {
                  setSelectedSlug(pkg.slug);
                  setConfirmDialog({ ...pkg, type: "spark" });
                }}
              />
            ))}
          </div>
        )}
      </PageCard>

      {/* Subscription Plans – tabbed by billing cycle */}
      {subscriptionPlans.length > 0 && (
        <PageCard>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold theme-text">
                {t("spark_pool.subscriptionPlans")}
              </h3>
              <p className="mt-1 text-sm theme-text-muted">
                {t("spark_pool.subscriptionPlansDesc")}
              </p>
            </div>
          </div>

          {/* Tabs */}
          <div className="mt-4 flex gap-2 overflow-x-auto pb-1">
            {BILLING_CYCLES.map((cycle) => (
              <button
                key={cycle}
                onClick={() => setActiveCycle(cycle)}
                className={`shrink-0 rounded-full px-4 py-1.5 text-xs font-medium capitalize transition ${
                  activeCycle === cycle
                    ? "bg-[var(--color-accent)] text-white"
                    : "bg-[var(--color-soft)] theme-text"
                }`}
              >
                {t(`spark_pool.${cycle}`)}
              </button>
            ))}
          </div>

          {/* Plans for the active cycle */}
          <div className="mt-3 space-y-3">
            {plansByCycle[activeCycle]?.length === 0 && (
              <p className="py-4 text-center text-sm theme-text-muted">
                {t("spark_pool.noPlansForCycle").replace(
                  "{cycle}",
                  t(`spark_pool.${activeCycle}`),
                )}
              </p>
            )}
            {plansByCycle[activeCycle]?.map((plan) => (
              <SubscriptionPlanCard
                key={plan.slug}
                plan={plan}
                isSelected={plan.slug === selectedSlug}
                hasActiveSubscription={!!activeSubscription}
                onSelect={() => {
                  if (!isAuthenticated) return;
                  setSelectedSlug(plan.slug);
                  setConfirmDialog(plan);
                }}
              />
            ))}
          </div>
        </PageCard>
      )}

      {/* Recent Transactions */}
      {isAuthenticated && (
        <PageCard>
          <SectionTitle
            action={
              <button
                className="text-xs font-medium"
                style={{ color: "var(--color-accent)" }}
                onClick={() => navigate("/transactions")}
              >
                {t("common.viewAll")}
              </button>
            }
          >
            {t("spark_pool.recentTransactions")}
          </SectionTitle>

          {transactions.length === 0 ? (
            <p className="py-4 text-center text-sm theme-text-muted">
              {t("spark_pool.noTransactionsYet")}
            </p>
          ) : (
            <div
              className="mt-4 overflow-x-auto rounded-xl border"
              style={{ borderColor: "var(--color-border)" }}
            >
              <table className="min-w-full text-left text-sm">
                <thead
                  className="text-xs"
                  style={{
                    backgroundColor: "var(--color-soft)",
                    color: "var(--color-text-muted)",
                  }}
                >
                  <tr>
                    <th className="px-4 py-3 font-medium">
                      {t("spark_pool.description")}
                    </th>
                    <th className="px-4 py-3 font-medium text-right">
                      {t("spark_pool.amount")}
                    </th>
                    <th className="px-4 py-3 font-medium text-right hidden sm:table-cell">
                      {t("spark_pool.date")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.map((tx) => (
                    <tr
                      key={tx.id}
                      className="transition-colors hover:bg-[var(--color-soft)]"
                      style={{ borderBottom: "1px solid var(--color-border)" }}
                    >
                      <td className="px-4 py-3">
                        <p className="font-medium theme-text">
                          {tx.description}
                        </p>
                        <p className="text-xs theme-text-muted sm:hidden mt-0.5">
                          {formatRelativeTime(tx.created_at, t)}
                        </p>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums theme-text">
                        {tx.type === "credit" ? "+" : "-"}
                        {Math.abs(Number(tx.amount))} {tx.currency}
                      </td>
                      <td className="px-4 py-3 text-right text-xs theme-text-muted hidden sm:table-cell">
                        {formatRelativeTime(tx.created_at, t)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </PageCard>
      )}

      {/* Confirmation Dialog */}
      {confirmDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative w-full max-w-sm rounded-2xl p-6 shadow-xl theme-card">
            <h3 className="text-lg font-semibold theme-text">
              {t("spark_pool.confirmPurchase")}
            </h3>
            {confirmDialog.type === "spark" ? (
              <p className="mt-2 text-sm theme-text-muted">
                {t("spark_pool.purchaseSparkConfirmation")
                  .replace("{sparks}", confirmDialog.base_spark_amount)
                  .replace(
                    "{price}",
                    `KES ${Number(confirmDialog.final_price_kes).toFixed(2)}`,
                  )}
              </p>
            ) : (
              <div className="mt-2 text-sm theme-text-muted space-y-1">
                {activeSubscription ? (
                  <>
                    <p>
                      {t("spark_pool.upgradeSubscriptionConfirmation").replace(
                        "{planName}",
                        confirmDialog.name,
                      )}
                    </p>
                    <p>
                      {t("spark_pool.replaceSubscriptionNotice")
                        .replace("{currentPlan}", activeSubscription.plan_name)
                        .replace(
                          "{remainingSparks}",
                          activeSubscription.sparks_remaining?.toLocaleString(),
                        )}
                    </p>
                    <p>
                      {t("spark_pool.billingInfo")
                        .replace("{cycle}", confirmDialog.billing_cycle)
                        .replace(
                          "{price}",
                          `KES ${Number(confirmDialog.price).toFixed(2)}`,
                        )}
                    </p>
                  </>
                ) : (
                  <>
                    <p>
                      {t("spark_pool.newSubscriptionConfirmation").replace(
                        "{planName}",
                        confirmDialog.name,
                      )}
                    </p>
                    <p>
                      {t("spark_pool.billingInfo")
                        .replace("{cycle}", confirmDialog.billing_cycle)
                        .replace(
                          "{price}",
                          `KES ${Number(confirmDialog.price).toFixed(2)}`,
                        )}
                    </p>
                    <p>
                      {t("spark_pool.planIncludes").replace(
                        "{sparks}",
                        confirmDialog.sparks,
                      )}
                    </p>
                  </>
                )}
              </div>
            )}
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setConfirmDialog(null)}
                className="rounded-lg px-4 py-2 text-sm font-medium transition-colors"
                style={{
                  backgroundColor: "var(--color-soft)",
                  color: "var(--color-text-muted)",
                }}
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={handlePurchaseConfirm}
                disabled={loadingPayment === confirmDialog.slug}
                className="rounded-lg px-4 py-2 text-sm font-medium text-white transition-opacity"
                style={{
                  backgroundColor: "var(--color-accent)",
                  opacity: loadingPayment === confirmDialog.slug ? 0.5 : 1,
                }}
              >
                {loadingPayment === confirmDialog.slug
                  ? t("common.processing")
                  : t("common.confirm")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
