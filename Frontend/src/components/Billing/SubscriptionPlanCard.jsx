// src/components/Billing/SubscriptionPlanCard.jsx
import StatusBadge from "../StatusBadge";

export default function SubscriptionPlanCard({
  plan,
  isSelected,
  hasActiveSubscription,
  onSelect,
}) {
  return (
    <div
      onClick={onSelect}
      className={`w-full rounded-2xl p-4 text-left transition cursor-pointer ${
        hasActiveSubscription ? "opacity-70" : ""
      }`}
      style={{
        border: `1px solid ${isSelected ? "var(--color-accent)" : "var(--color-border)"}`,
        backgroundColor: isSelected
          ? "var(--color-accent-soft)"
          : "var(--color-soft)",
      }}
    >
      <div className="flex items-center gap-2 mb-2">
        <p className="text-base font-semibold theme-text">{plan.name}</p>
        <StatusBadge status={plan.billing_cycle} />
        <p
          className="ml-auto text-sm font-semibold"
          style={{ color: "var(--color-accent)" }}
        >
          KES {Number(plan.price).toFixed(2)}/{plan.billing_cycle}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        <span className="theme-text-muted">Sparks:</span>
        <span className="text-right theme-text">{plan.sparks}</span>
        <span className="theme-text-muted">Msg Pairs:</span>
        <span className="text-right theme-text">
          {plan.message_pairs ?? "∞"}
        </span>
        <span className="theme-text-muted">Characters:</span>
        <span className="text-right theme-text">{plan.characters ?? "∞"}</span>
        <span className="theme-text-muted">Images:</span>
        <span className="text-right theme-text">{plan.images ?? "∞"}</span>
      </div>
    </div>
  );
}
