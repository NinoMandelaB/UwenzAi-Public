// src/components/Billing/SparkPackageCard.jsx
import StatusBadge from "../StatusBadge";
import { useLanguage } from "../../context/LanguageContext";

export default function SparkPackageCard({
  pkg,
  isSelected,
  onSelect,
  playPrice = null,
}) {
  const { t } = useLanguage();

  return (
    <div
      onClick={onSelect}
      className={`w-full rounded-2xl p-4 text-left transition cursor-pointer ${
        isSelected ? "border-accent bg-accent-soft" : "border-border bg-soft"
      }`}
      style={
        isSelected
          ? {
              borderColor: "var(--color-accent)",
              backgroundColor: "var(--color-accent-soft)",
            }
          : {
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-soft)",
            }
      }
    >
      <div className="flex items-start gap-2 mb-2">
        <p className="text-base font-semibold theme-text">
          {pkg.base_spark_amount} {t("spark_pool.sparks")}
        </p>
        {pkg.discount_percent > 0 && (
          <StatusBadge status={`-${pkg.discount_percent}%`} />
        )}
        <div className="ml-auto text-right">
          <p
            className="text-sm font-semibold"
            style={{ color: "var(--color-accent)" }}
          >
            {/* Inside the Android app the authoritative price is whatever
                Google Play charges in the customer's region and currency;
                the KES price must never be advertised alongside it. */}
            {playPrice ?? `KES ${Number(pkg.final_price_kes).toFixed(2)}`}
          </p>
          {!playPrice && (pkg.final_price_usd || pkg.final_price_eur) && (
            <p
              className="text-xs theme-text-muted"
              title={t("spark_pool.approxConverted")}
            >
              ≈{" "}
              {pkg.final_price_usd &&
                `$${Number(pkg.final_price_usd).toFixed(2)}`}
              {pkg.final_price_usd && pkg.final_price_eur && " · "}
              {pkg.final_price_eur &&
                `€${Number(pkg.final_price_eur).toFixed(2)}`}
            </p>
          )}
        </div>
      </div>
      {!playPrice && Number(pkg.vat_percent) > 0 && (
        <p className="text-xs theme-text-muted -mt-1 mb-2">
          {t("spark_pool.vatIncluded").replace(
            "{vat}",
            Number(pkg.vat_percent).toFixed(0),
          )}
        </p>
      )}
      <p className="text-sm theme-text-muted">
        {pkg.description || t("spark_pool.packageUsageText")}
      </p>
    </div>
  );
}
