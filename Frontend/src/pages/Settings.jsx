// src/pages/Settings.jsx

import { useEffect, useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import {
  Bell,
  ChevronRight,
  Sparkles as SparkPool,
  Globe,
  Info,
  MoonStar,
  PlusCircle,
  Shield,
  Trash2,
  UserRound,
  X,
  FileText,
  Lock,
  BookOpen,
} from "lucide-react";

import PrimaryButton from "../components/PrimaryButton";
import AdminQuickActions from "../components/AdminQuickActions";
import SectionTitle from "../components/SectionTitle";
import MessagePopup from "../components/MessagePopup";
import DeleteAccountDialog from "../components/DeleteAccountDialog";
import SupportSection from "../components/SupportSection";

import { useTheme } from "../context/ThemeContext";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";

import { deleteAccount } from "../services/authService";
import { getAuthMessage } from "../utils/authMessageMapper";
import { STORAGE_KEYS } from "../config/constants";
import { getPricing, getSparkPoolBalance } from "../services/billingService";
import { clearAccessToken } from "../services/apiClient";
import BYOKSection from "../components/BYOKSection";

function ToggleItem({ title, subtitle, checked, onChange, icon: Icon, t }) {
  return (
    <div className="rounded-2xl border p-4 theme-card">
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
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

          <div className="min-w-0">
            <p className="text-sm font-semibold theme-text">{title}</p>
            <p className="mt-1 text-sm leading-6 theme-text-muted">
              {subtitle}
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={onChange}
          className={`relative h-7 w-12 shrink-0 rounded-full transition ${
            checked ? "bg-[var(--color-accent)]" : "bg-slate-500/60"
          }`}
          aria-pressed={checked}
        >
          <span
            className={`absolute top-1 h-5 w-5 rounded-full bg-white transition ${
              checked ? "left-6" : "left-1"
            }`}
          />
        </button>
      </div>
    </div>
  );
}

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

function LanguageToggle({ t }) {
  const { language, setLanguage } = useLanguage();

  return (
    <div className="rounded-2xl border p-4 theme-card">
      <div className="flex items-start gap-3">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border"
          style={{
            backgroundColor: "var(--color-soft)",
            borderColor: "var(--color-border)",
            color: "var(--color-accent)",
          }}
        >
          <Globe size={18} />
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold theme-text">
            {t("common.language")}
          </p>
          <p className="mt-1 text-sm leading-6 theme-text-muted">
            {language === "en" ? t("common.english") : t("common.swahili")}
          </p>

          <div
            className="mt-4 inline-flex items-center gap-2 rounded-2xl border p-2"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-soft)",
            }}
          >
            <button
              type="button"
              onClick={() => setLanguage("en")}
              className="rounded-full px-4 py-2 text-xs font-semibold transition"
              style={{
                backgroundColor:
                  language === "en" ? "var(--color-accent)" : "transparent",
                color:
                  language === "en" ? "#082f49" : "var(--color-text-muted)",
              }}
            >
              {t("common.english")}
            </button>

            <button
              type="button"
              onClick={() => setLanguage("sw")}
              className="rounded-full px-4 py-2 text-xs font-semibold transition"
              style={{
                backgroundColor:
                  language === "sw" ? "var(--color-accent)" : "transparent",
                color:
                  language === "sw" ? "#082f49" : "var(--color-text-muted)",
              }}
            >
              {t("common.swahili")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function CreateCharacterCTA({ onClick, t }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center justify-center gap-2 rounded-2xl border px-4 py-3 text-base font-semibold transition hover:opacity-90 active:scale-[0.99]"
      style={{
        borderColor: "var(--color-accent)",
        backgroundColor: "var(--color-accent-soft)",
        color: "var(--color-accent)",
      }}
    >
      <PlusCircle size={20} />
      {t("characters.createButton")}
    </button>
  );
}

function DeleteAccountCard({ onClick, t }) {
  return (
    <div
      className="rounded-3xl border p-4 shadow-xl"
      style={{
        borderColor: "rgba(244, 63, 94, 0.25)",
        background:
          "linear-gradient(180deg, rgba(244, 63, 94, 0.06) 0%, rgba(244, 63, 94, 0.03) 100%)",
      }}
    >
      <div className="mb-4 flex items-start gap-3">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl"
          style={{
            backgroundColor: "rgba(244, 63, 94, 0.12)",
            color: "rgb(225, 29, 72)",
            border: "1px solid rgba(244, 63, 94, 0.18)",
          }}
        >
          <Trash2 size={18} />
        </div>

        <div className="min-w-0">
          <h3 className="text-sm font-semibold theme-text">
            {t("settings.permanentlyDeleteAccount")}
          </h3>
          <p className="mt-1 text-sm leading-6 theme-text-muted">
            {t("settings.deleteAccountDescription")}
          </p>
        </div>
      </div>

      <button
        type="button"
        onClick={onClick}
        className="w-full rounded-2xl border p-4 text-left transition hover:opacity-95"
        style={{
          borderColor: "rgba(244, 63, 94, 0.35)",
          background:
            "linear-gradient(180deg, rgba(244, 63, 94, 0.08) 0%, rgba(244, 63, 94, 0.05) 100%)",
        }}
      >
        <div className="flex items-center gap-3">
          <div
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl"
            style={{
              backgroundColor: "rgba(244, 63, 94, 0.14)",
              color: "rgb(225, 29, 72)",
              border: "1px solid rgba(244, 63, 94, 0.22)",
            }}
          >
            <Trash2 size={18} />
          </div>

          <div className="min-w-0 flex-1">
            <p
              className="text-sm font-semibold"
              style={{ color: "rgb(255, 115, 145)" }}
            >
              {t("settings.deleteAccountPermanently")}
            </p>
            <p className="mt-0.5 text-xs leading-tight theme-text-muted">
              {t("settings.deleteAccountWarning")}
            </p>
          </div>

          <div
            className="shrink-0 flex items-center"
            style={{ color: "rgb(255, 115, 145)" }}
          >
            <ChevronRight size={18} />
          </div>
        </div>
      </button>
    </div>
  );
}

export default function Settings() {
  const navigate = useNavigate();
  const location = useLocation();

  const { isDark, toggleTheme } = useTheme();
  const { t, language } = useLanguage();
  const { user, isAuthenticated, isAdmin } = useAuth();

  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isDeletingAccount, setIsDeletingAccount] = useState(false);
  const [deleteAccountError, setDeleteAccountError] = useState("");

  const [successMessage, setSuccessMessage] = useState(
    location.state?.successMessage || "",
  );
  const [showSuccessPopup, setShowSuccessPopup] = useState(
    Boolean(location.state?.successMessage),
  );

  const [showPolicies, setShowPolicies] = useState(false);

  // Cost confirmation dialog state
  const [showCostDialog, setShowCostDialog] = useState(false);
  const [costData, setCostData] = useState(null);
  const [sparkPoolBalance, setSparkPoolBalance] = useState(0);
  const [isLoadingCosts, setIsLoadingCosts] = useState(false);
  const [costError, setCostError] = useState("");

  useEffect(() => {
    if (location.state?.successMessage) {
      setSuccessMessage(location.state.successMessage);
      setShowSuccessPopup(true);

      navigate(location.pathname, {
        replace: true,
        state: {},
      });
    }
  }, [location.pathname, location.state, navigate]);

  const displayName =
    user?.display_name ||
    user?.full_name ||
    user?.username ||
    t("common.guestUser");

  const initials = (
    displayName
      .split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("") || "G"
  ).toUpperCase();

  const currentTheme = isDark
    ? t("settings.themeDark")
    : t("settings.themeLight");
  const currentLanguage =
    language === "en" ? t("common.english") : t("common.swahili");

  const handleCloseSuccessPopup = () => {
    setShowSuccessPopup(false);
  };

  const handleOpenDeleteDialog = () => {
    setDeleteAccountError("");
    setIsDeleteDialogOpen(true);
  };

  const handleCloseDeleteDialog = () => {
    if (isDeletingAccount) return;
    setDeleteAccountError("");
    setIsDeleteDialogOpen(false);
  };

  const handleDeleteAccount = async () => {
    if (isDeletingAccount) return;

    try {
      setIsDeletingAccount(true);
      setDeleteAccountError("");

      await deleteAccount();

      clearAccessToken();
      localStorage.removeItem(STORAGE_KEYS.ACCESS_TOKEN);

      setIsDeleteDialogOpen(false);

      navigate("/login", {
        replace: true,
        state: {
          successMessage:
            "Your account has been permanently deleted. This action cannot be undone.",
        },
      });
    } catch (error) {
      setDeleteAccountError(
        getAuthMessage(
          error,
          "We could not delete your account right now. Please try again.",
        ),
      );
    } finally {
      setIsDeletingAccount(false);
    }
  };

  const handleCreateCharacterClick = async () => {
    if (!isAuthenticated) return;

    if (isAdmin) {
      navigate("/characters/create");
      return;
    }

    setIsLoadingCosts(true);
    setCostError("");
    try {
      const [pricingRes, balanceRes] = await Promise.all([
        getPricing(),
        getSparkPoolBalance(),
      ]);
      const pricing = pricingRes.data;
      const balance = balanceRes.data?.sparks || 0;
      setCostData({
        characterCost: pricing.character_generation_cost,
        imageCost: pricing.image_generation_cost,
        cashbackAmount: pricing.cashback_public_character,
      });
      setSparkPoolBalance(balance);
      setShowCostDialog(true);
    } catch (err) {
      setCostError(t("characters.loadPricingError"));
    } finally {
      setIsLoadingCosts(false);
    }
  };

  const handleProceedToCreate = () => {
    if (sparkPoolBalance < costData?.characterCost) {
      const template = t("characters.insufficientSparks");
      const message = template.replace("{cost}", costData.characterCost);
      setCostError(message);
      return;
    }
    setShowCostDialog(false);
    navigate("/characters/create");
  };

  const isBalanceSufficient =
    costData && sparkPoolBalance >= costData.characterCost;

  return (
    <>
      <MessagePopup
        open={showSuccessPopup && Boolean(successMessage)}
        message={successMessage}
        type="success"
        title={t("settings.success")}
        onClose={handleCloseSuccessPopup}
      />

      <DeleteAccountDialog
        isOpen={isDeleteDialogOpen}
        isDeleting={isDeletingAccount}
        errorMessage={deleteAccountError}
        onClose={handleCloseDeleteDialog}
        onConfirm={handleDeleteAccount}
      />

      {/* Terms & Privacy Modal (moved from SupportSection) */}
      {showPolicies && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-sm rounded-2xl p-6 shadow-lg theme-card">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-lg font-semibold theme-text">
                {t("settings.termsAndPolicies")}
              </h3>
              <button
                onClick={() => setShowPolicies(false)}
                className="rounded-full p-1 text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
              >
                <X size={20} />
              </button>
            </div>

            <div className="space-y-2">
              <Link
                to="/terms"
                onClick={() => setShowPolicies(false)}
                className="flex items-center gap-3 rounded-xl border p-3 theme-card hover:opacity-90 transition"
              >
                <FileText size={18} className="text-[var(--color-accent)]" />
                <span className="text-sm font-medium theme-text">
                  {t("settings.termsOfService")}
                </span>
              </Link>

              <Link
                to="/privacy"
                onClick={() => setShowPolicies(false)}
                className="flex items-center gap-3 rounded-xl border p-3 theme-card hover:opacity-90 transition"
              >
                <Lock size={18} className="text-[var(--color-accent)]" />
                <span className="text-sm font-medium theme-text">
                  {t("settings.privacyPolicy")}
                </span>
              </Link>

              <Link
                to="/responsible-use"
                onClick={() => setShowPolicies(false)}
                className="flex items-center gap-3 rounded-xl border p-3 theme-card hover:opacity-90 transition"
              >
                <BookOpen size={18} className="text-[var(--color-accent)]" />
                <span className="text-sm font-medium theme-text">
                  {t("settings.responsibleUseGuide")}
                </span>
              </Link>
            </div>
          </div>
        </div>
      )}

      <div className="space-y-6 max-w-2xl">
        {!isAuthenticated && (
          <section className="rounded-3xl border p-5 shadow-xl theme-card">
            <h2 className="text-lg font-semibold theme-text">
              {t("settings.guestModeTitle")}
            </h2>

            <p className="mt-2 text-sm leading-6 theme-text-muted">
              {t("settings.guestModeText")}
            </p>

            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <PrimaryButton
                variant="secondary"
                fullWidth
                onClick={() => navigate("/login")}
              >
                {t("common.login")}
              </PrimaryButton>

              <PrimaryButton fullWidth onClick={() => navigate("/register")}>
                {t("common.createAccount")}
              </PrimaryButton>
            </div>
          </section>
        )}

        <section className="rounded-3xl border p-5 shadow-xl theme-card">
          <div className="flex items-center justify-between gap-4">
            <div className="flex min-w-0 items-center gap-4">
              <div
                className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full text-lg font-semibold"
                style={{
                  backgroundColor: "var(--color-accent-soft)",
                  color: "var(--color-accent)",
                }}
              >
                {initials}
              </div>

              <div className="min-w-0">
                <h2 className="text-lg font-semibold theme-text">
                  {displayName}
                </h2>
                <p className="mt-1 text-sm theme-text-muted">
                  {isAuthenticated
                    ? t("common.signedIn")
                    : t("common.guestMode")}
                </p>
              </div>
            </div>

            {isAuthenticated && (
              <button
                type="button"
                onClick={() => navigate("/spark_pool")}
                className="inline-flex shrink-0 items-center gap-2 rounded-full px-3 py-2 text-xs font-semibold transition hover:opacity-95"
                style={{
                  backgroundColor: "var(--color-accent-soft)",
                  color: "var(--color-accent)",
                  border: "1px solid var(--color-border)",
                }}
              >
                <SparkPool size={14} />
                <span>{t("settings.mySparkPool")}</span>
              </button>
            )}
          </div>
        </section>

        <section className="space-y-3">
          <SectionTitle>{t("settings.preferences")}</SectionTitle>

          <div className="space-y-3">
            <LanguageToggle t={t} />

            <ToggleItem
              title={t("settings.darkMode")}
              subtitle={currentTheme}
              checked={isDark}
              onChange={toggleTheme}
              icon={MoonStar}
              t={t}
            />
          </div>
        </section>

        <section className="space-y-3">
          <SectionTitle>{t("settings.privacyAndSafety")}</SectionTitle>

          <div className="space-y-3">
            <SettingsItem
              title={t("settings.termsAndPrivacy")}
              subtitle={t("settings.platformPolicies")}
              icon={Shield}
              onClick={() => setShowPolicies(true)}
            />
          </div>
        </section>

        {isAuthenticated && (
          <section className="space-y-4">
            <BYOKSection />

            <CreateCharacterCTA onClick={handleCreateCharacterClick} t={t} />

            {isAdmin && <AdminQuickActions />}

            <DeleteAccountCard onClick={handleOpenDeleteDialog} t={t} />
          </section>
        )}

        {!isAuthenticated && (
          <section className="space-y-3">
            <SectionTitle>{t("settings.account")}</SectionTitle>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <PrimaryButton
                variant="secondary"
                fullWidth
                onClick={() => navigate("/login")}
              >
                {t("common.login")}
              </PrimaryButton>

              <PrimaryButton fullWidth onClick={() => navigate("/register")}>
                {t("common.createAccount")}
              </PrimaryButton>
            </div>
          </section>
        )}
      </div>

      {/* Cost Confirmation Dialog */}
      {showCostDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm transition-all"
          onClick={(e) => {
            if (e.target === e.currentTarget && !isLoadingCosts)
              setShowCostDialog(false);
          }}
        >
          <div
            className="w-full max-w-md rounded-2xl p-6 shadow-xl transition-all"
            style={{
              backgroundColor: "var(--color-shell)",
              border: "1px solid var(--color-border)",
            }}
          >
            <h3 className="text-xl font-semibold theme-text mb-3">
              {t("characters.costDialogTitle")}
            </h3>

            {isLoadingCosts ? (
              <div className="py-8 text-center">
                <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-current border-t-transparent theme-text-muted" />
                <p className="mt-2 text-sm theme-text-muted">
                  {t("characters.loadingPricing")}
                </p>
              </div>
            ) : costError ? (
              <>
                <p className="text-sm text-red-500 mb-4">{costError}</p>
                <button
                  onClick={() => setShowCostDialog(false)}
                  className="w-full rounded-xl bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90"
                  style={{ backgroundColor: "var(--color-accent)" }}
                >
                  {t("common.close")}
                </button>
              </>
            ) : (
              <>
                <div className="space-y-3 mb-6">
                  <div
                    className="rounded-xl p-4"
                    style={{ backgroundColor: "var(--color-card)" }}
                  >
                    <p className="text-sm theme-text-muted mb-2">
                      {t("characters.creationCostDescription").replace(
                        "{cost}",
                        costData.characterCost,
                      )}{" "}
                      {t("spark_pool.sparks")}.
                    </p>
                    <ul className="text-sm theme-text-muted list-disc list-inside space-y-1 ml-1">
                      <li>{t("characters.costIncludesProfile")}</li>
                      <li>{t("characters.costIncludesAvatar")}</li>
                    </ul>
                    <p className="text-sm theme-text-muted mt-3">
                      {t("characters.additionalImageCost").replace(
                        "{cost}",
                        costData.imageCost,
                      )}{" "}
                      {t("spark_pool.sparks")}.
                    </p>
                    <p className="text-sm theme-text-muted mt-3">
                      {t("characters.cashbackInfo").replace(
                        "{amount}",
                        costData.cashbackAmount,
                      )}
                    </p>
                    <div className="mt-3 pt-3 border-t theme-border flex justify-between text-sm">
                      <span className="theme-text-muted">
                        {t("characters.yourBalance")}
                      </span>
                      <span
                        className={`font-semibold ${
                          isBalanceSufficient
                            ? "text-green-600"
                            : "text-red-500"
                        }`}
                      >
                        {sparkPoolBalance} {t("spark_pool.sparks")}
                      </span>
                    </div>
                    {!isBalanceSufficient && (
                      <p className="text-xs text-red-500 mt-2">
                        {t("characters.insufficientSparks").replace(
                          "{cost}",
                          costData.characterCost,
                        )}
                      </p>
                    )}
                  </div>
                </div>

                <div className="flex gap-3">
                  <button
                    onClick={() => setShowCostDialog(false)}
                    className="flex-1 rounded-xl border px-4 py-2 text-sm font-medium transition hover:opacity-80"
                    style={{
                      borderColor: "var(--color-border)",
                      color: "var(--color-text-muted)",
                    }}
                  >
                    {t("common.cancel")}
                  </button>
                  <button
                    onClick={handleProceedToCreate}
                    disabled={!isBalanceSufficient}
                    className="flex-1 rounded-xl px-4 py-2 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed"
                    style={{
                      backgroundColor: "var(--color-accent)",
                      color: "#082f49",
                    }}
                  >
                    {t("characters.proceedToCreate")}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
