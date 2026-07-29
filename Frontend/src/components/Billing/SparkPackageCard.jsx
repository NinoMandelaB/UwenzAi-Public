// src/components/Billing/SparkPackageCard.jsx
import StatusBadge from "../StatusBadge";
import { useLanguage } from "../../context/LanguageContext";

export default function SparkPackageCard({ pkg, isSelected, onSelect }) {
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
      <div className="flex items-center gap-2 mb-2">
        <p className="text-base font-semibold theme-text">
          {pkg.base_spark_amount} {t("spark_pool.sparks")}
        </p>
        {pkg.discount_percent > 0 && (
          <StatusBadge status={`-${pkg.discount_percent}%`} />
        )}
        <p
          className="ml-auto text-sm font-semibold"
          style={{ color: "var(--color-accent)" }}
        >
          KES {Number(pkg.final_price_kes).toFixed(2)}
        </p>
      </div>
      <p className="text-sm theme-text-muted">
        {pkg.description || t("spark_pool.packageUsageText")}
      </p>
    </div>
  );
}
