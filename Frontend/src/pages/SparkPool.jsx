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
  getSparkTransactions,
  getCurrentSubscription,
} from "../services/billingService";
import { ensureGuestSession } from "../utils/session";
import { useSparkPoolBalance } from "../hooks/useSparkPoolBalance";
import {
  getPlayBillingService,
  fetchPlayItemDetails,
  formatPlayPrice,
  purchaseSparksViaPlay,
  recoverPendingPlayPurchases,
  PLAY_ERRORS,
} from "../services/playBillingService";
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

// Play verify result -> notice shown after the purchase sheet closes.
const PLAY_NOTICE_FOR_RESULT = {
  credited: "success",
  already_credited: "already",
  pending: "pending",
  cancelled: "cancelled",
};

const PLAY_NOTICE_TONE = {
  success: "var(--color-success, #22c55e)",
  already: "var(--color-success, #22c55e)",
  pending: "var(--color-warning, #f59e0b)",
  cancelled: "var(--color-warning, #f59e0b)",
  error: "var(--color-danger, #ef4444)",
};

export default function SparkPool() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { isAuthenticated, user } = useAuth();

  // The same balance the header and the chat window show, guest or account: a
  // second reader here is a second thing to go stale, and the guest copy used
  // to read the account's cache key on top of that.
  const { balance } = useSparkPoolBalance();

  const [selectedSlug, setSelectedSlug] = useState(null);
  const [packages, setPackages] = useState([]);
  const [transactions, setTransactions] = useState([]);
  const [activeSubscription, setActiveSubscription] = useState(null);
  const [loadingPackages, setLoadingPackages] = useState(true);
  const [loadingPayment, setLoadingPayment] = useState(null);
  const [confirmDialog, setConfirmDialog] = useState(null);

  // Google Play Billing inside the Android TWA. `playService` is non-null only
  // inside the app; on the plain website it resolves to null and the existing
  // Paystack flow below is untouched. `playDetails` is null until the Play
  // prices have loaded, so the package list can tell "still loading" from
  // "nothing purchasable".
  const [playService, setPlayService] = useState(null);
  const [playDetails, setPlayDetails] = useState(null);
  const [playNotice, setPlayNotice] = useState(null);

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

  useEffect(() => {
    fetchPackages();
    if (isAuthenticated) {
      fetchTransactions();
      fetchSubscription();
    }
  }, [isAuthenticated, fetchPackages, fetchTransactions, fetchSubscription]);

  // Detect Play Billing once. The check itself doubles as "am I the Android
  // app?": the Digital Goods API only exists inside the TWA.
  useEffect(() => {
    let cancelled = false;
    getPlayBillingService().then((service) => {
      if (!cancelled) setPlayService(service);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Inside the app, prices come from the Play Console — never the KES price.
  useEffect(() => {
    if (!playService || loadingPackages) return;
    const productIds = packages
      .filter((p) => p.type === "spark" && p.play_product_id)
      .map((p) => p.play_product_id);
    if (productIds.length === 0) {
      setPlayDetails({});
      return;
    }
    fetchPlayItemDetails(productIds)
      .then((details) => setPlayDetails(details))
      .catch(() => setPlayDetails({}));
  }, [playService, loadingPackages, packages]);

  // Purchase recovery: if the app closed between the Play sheet and the
  // backend call, the purchase is still uncredited. Verifying it now is
  // idempotent (a re-submitted token credits once), and consuming frees the
  // product for the next purchase. It also runs on every app open
  // (PlayPurchaseRecovery); overlapping calls share one run.
  useEffect(() => {
    if (!playService || !isAuthenticated) return;
    recoverPendingPlayPurchases().catch((err) =>
      console.error("Play purchase recovery failed", err),
    );
  }, [playService, isAuthenticated]);

  const handlePurchaseConfirm = async () => {
    if (!confirmDialog) return;
    const packageSlug = confirmDialog.slug;
    const packageType = confirmDialog.type;
    setLoadingPayment(packageSlug);
    setConfirmDialog(null);
    try {
      if (playService) {
        // Android app: Google Play Billing only. Per Play policy the web
        // checkout is never offered inside the app, so there is no Paystack
        // fallback on this branch.
        if (!isAuthenticated) {
          navigate("/login");
          return;
        }
        const outcome = await purchaseSparksViaPlay({
          service: playService,
          productId: confirmDialog.play_product_id,
        });
        setPlayNotice({
          kind: PLAY_NOTICE_FOR_RESULT[outcome.result] ?? "error",
          sparks: outcome.sparksGranted,
        });
      } else {
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
      }
    } catch (err) {
      if (err?.code === PLAY_ERRORS.USER_CANCELLED) {
        // The user dismissed Google's own sheet; nothing happened and no
        // message is needed beyond restoring the normal state.
        setPlayNotice(null);
      } else if (playService) {
        console.error("Play purchase failed", err);
        setPlayNotice({ kind: "error" });
      } else {
        console.error("Payment initiation failed", err);
      }
    } finally {
      setLoadingPayment(null);
    }
  };

  const sparkPackages = packages.filter((p) => p.type === "spark");
  const subscriptionPlans = packages.filter((p) => p.type === "subscription");

  // Play policy: inside the Android app every purchase goes through Google
  // Play, so only packages mapped to a Play product with a live Play price are
  // shown — never the Paystack/KES flow as a fallback. Subscriptions are not
  // offered in the app at all until they are implemented through Play Billing.
  const visibleSparkPackages = playService
    ? sparkPackages.filter(
        (p) => p.play_product_id && playDetails[p.play_product_id],
      )
    : sparkPackages;

  const playPriceFor = (pkg) => {
    if (!playService || !pkg.play_product_id || !playDetails) return null;
    const details = playDetails[pkg.play_product_id];
    if (!details) return null;
    return formatPlayPrice(details.currency, details.price);
  };

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
          {balance === null ? "..." : balance.toLocaleString()}{" "}
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

      {/* Play purchase outcome (Android app only) */}
      {playNotice && (
        <section
          className="rounded-3xl border p-4 shadow-sm"
          style={{
            borderColor: `color-mix(in srgb, ${PLAY_NOTICE_TONE[playNotice.kind]} 25%, transparent)`,
            backgroundColor: `color-mix(in srgb, ${PLAY_NOTICE_TONE[playNotice.kind]} 8%, transparent)`,
          }}
        >
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm theme-text leading-6">
              {playNotice.kind === "success" &&
                t("spark_pool.playPurchaseSuccess").replace(
                  "{sparks}",
                  playNotice.sparks?.toLocaleString(),
                )}
              {playNotice.kind === "already" &&
                t("spark_pool.playPurchaseAlready")}
              {playNotice.kind === "pending" &&
                t("spark_pool.playPurchasePending")}
              {playNotice.kind === "cancelled" &&
                t("spark_pool.playPurchaseCancelled")}
              {playNotice.kind === "error" &&
                t("spark_pool.playPurchaseFailed")}
            </p>
            <button
              onClick={() => setPlayNotice(null)}
              className="text-xs font-medium theme-text-muted"
            >
              {t("common.close")}
            </button>
          </div>
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
        ) : playService && playDetails === null ? (
          // Play prices are still loading — "no packages available" would be
          // wrong here, and the KES price must not be shown in its place.
          <div className="py-8 text-center text-sm theme-text-muted">
            {t("spark_pool.loadingPackages")}
          </div>
        ) : visibleSparkPackages.length === 0 ? (
          <div className="py-8 text-center text-sm theme-text-muted">
            {t("spark_pool.noPackagesAvailable")}
          </div>
        ) : (
          <div className="mt-4 space-y-3">
            {visibleSparkPackages.map((pkg) => (
              <SparkPackageCard
                key={pkg.slug}
                pkg={pkg}
                isSelected={pkg.slug === selectedSlug}
                playPrice={playPriceFor(pkg)}
                onSelect={() => {
                  if (playService && !isAuthenticated) {
                    // Play purchases credit an account, not a guest cookie —
                    // inside the app, guests are sent to sign in instead of
                    // being offered the web checkout.
                    navigate("/login");
                    return;
                  }
                  setSelectedSlug(pkg.slug);
                  setConfirmDialog({
                    ...pkg,
                    type: "spark",
                    playPrice: playPriceFor(pkg),
                  });
                }}
              />
            ))}
          </div>
        )}
      </PageCard>

      {/* Subscription Plans – tabbed by billing cycle */}
      {/* Hidden inside the Android app: subscriptions are not yet sold through
          Play Billing, and offering the web checkout here is not allowed. */}
      {!playService && subscriptionPlans.length > 0 && (
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
              <>
                <p className="mt-2 text-sm theme-text-muted">
                  {t("spark_pool.purchaseSparkConfirmation")
                    .replace("{sparks}", confirmDialog.base_spark_amount)
                    .replace(
                      "{price}",
                      // Inside the app, the charged amount is Google Play's
                      // regional price — the KES figure is never shown.
                      confirmDialog.playPrice ??
                        `KES ${Number(confirmDialog.final_price_kes).toFixed(2)}`,
                    )}
                </p>
                {!confirmDialog.playPrice &&
                  Number(confirmDialog.vat_percent) > 0 && (
                    <p className="mt-1 text-xs theme-text-muted">
                      {t("spark_pool.vatIncluded").replace(
                        "{vat}",
                        Number(confirmDialog.vat_percent).toFixed(0),
                      )}
                    </p>
                  )}
                {confirmDialog.playPrice && (
                  <p className="mt-1 text-xs theme-text-muted">
                    {t("spark_pool.playBilledByGoogle")}
                  </p>
                )}
              </>
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
