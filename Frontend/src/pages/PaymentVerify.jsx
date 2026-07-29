// src/pages/PaymentVerify.jsx
import { useState, useEffect } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import PrimaryButton from "../components/PrimaryButton";
import { verifyPayment, verifyGuestPayment } from "../services/billingService";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";

export default function PaymentVerify() {
  const [searchParams] = useSearchParams();
  const reference = searchParams.get("reference");
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const { t } = useLanguage();

  // review = the payment went through but we could not deliver the sparks, so it
  // is queued for manual follow-up. Showing success here would be a lie.
  const [status, setStatus] = useState("loading"); // loading | success | review | failed
  const [balance, setBalance] = useState(null);

  useEffect(() => {
    if (!reference) {
      setStatus("failed");
      return;
    }

    let attempts = 0;
    const maxAttempts = 10;

    const interval = setInterval(async () => {
      try {
        let res;
        if (isAuthenticated) {
          res = await verifyPayment(reference);
        } else {
          res = await verifyGuestPayment(reference);
        }

        if (
          res.data.status === "success" &&
          res.data.fulfillment_state === "manual_review"
        ) {
          setStatus("review");
          clearInterval(interval);
        } else if (res.data.status === "success") {
          setStatus("success");
          setBalance(res.data.spark_pool_balance);
          // Update the cached balance in localStorage so AppHeader reflects it
          if (!isAuthenticated) {
            localStorage.setItem(
              "sparks_cache",
              String(res.data.spark_pool_balance),
            );
            window.dispatchEvent(
              new StorageEvent("storage", {
                key: "sparks_cache",
                newValue: String(res.data.spark_pool_balance),
              }),
            );
          }
          clearInterval(interval);
        } else if (
          res.data.status === "failed" ||
          res.data.status === "reversed"
        ) {
          setStatus("failed");
          clearInterval(interval);
        } else {
          attempts++;
          if (attempts >= maxAttempts) {
            setStatus("failed");
            clearInterval(interval);
          }
        }
      } catch {
        attempts++;
        if (attempts >= maxAttempts) {
          setStatus("failed");
          clearInterval(interval);
        }
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [reference, isAuthenticated]);

  return (
    <div className="mx-auto mt-16 max-w-lg p-6 text-center">
      {status === "loading" && (
        <>
          <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-4 border-sky-500 border-t-transparent" />
          <h2 className="theme-text text-lg font-semibold">
            {t("spark_pool.verifyingPayment")}
          </h2>
          <p className="theme-text-muted mt-2 text-sm">
            {t("spark_pool.verifyWaitMessage")}
          </p>
        </>
      )}

      {status === "success" && (
        <>
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-green-100 text-green-600 text-lg font-bold">
            ✓
          </div>
          <h2 className="theme-text text-lg font-semibold">
            {t("spark_pool.paymentSuccess")}
          </h2>
          <p className="theme-text-muted mt-2 text-sm">
            {t("spark_pool.sparksAdded")}
          </p>
          {balance !== null && (
            <p className="theme-text mt-4 text-lg font-semibold">
              {t("spark_pool.balance")}: {balance.toLocaleString()}{" "}
              {t("spark_pool.sparks")}
            </p>
          )}
          <div className="mt-6">
            <PrimaryButton
              onClick={() =>
                isAuthenticated ? navigate("/spark_pool") : navigate("/")
              }
            >
              {isAuthenticated
                ? t("spark_pool.goToSparkPool")
                : t("common.backToHome")}
            </PrimaryButton>
          </div>
        </>
      )}

      {status === "review" && (
        <>
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 text-amber-600 text-lg font-bold">
            !
          </div>
          <h2 className="theme-text text-lg font-semibold">
            {t("spark_pool.paymentUnderReview")}
          </h2>
          <p className="theme-text-muted mt-2 text-sm">
            {t("spark_pool.paymentUnderReviewMessage")}
          </p>
          <div className="mt-6">
            <PrimaryButton
              onClick={() =>
                isAuthenticated ? navigate("/spark_pool") : navigate("/")
              }
            >
              {isAuthenticated
                ? t("spark_pool.backToSparkPool")
                : t("common.backToHome")}
            </PrimaryButton>
          </div>
        </>
      )}

      {status === "failed" && (
        <>
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-red-600 text-lg font-bold">
            ✕
          </div>
          <h2 className="theme-text text-lg font-semibold">
            {t("spark_pool.paymentFailed")}
          </h2>
          <p className="theme-text-muted mt-2 text-sm">
            {t("spark_pool.verifyFailedMessage")}
          </p>
          <div className="mt-6">
            <PrimaryButton
              onClick={() =>
                isAuthenticated ? navigate("/spark_pool") : navigate("/")
              }
            >
              {isAuthenticated
                ? t("spark_pool.backToSparkPool")
                : t("common.backToHome")}
            </PrimaryButton>
          </div>
        </>
      )}
    </div>
  );
}
