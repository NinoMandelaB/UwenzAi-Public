// src/pages/details/shared.jsx

export function SectionTitle({ icon, title }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      {icon && <span style={{ color: "var(--color-accent)" }}>{icon}</span>}
      <h4 className="text-sm font-semibold theme-text">{title}</h4>
    </div>
  );
}

export function AttributeRow({ label, value }) {
  return (
    <div
      className="flex items-baseline justify-between py-2 border-b last:border-0"
      style={{ borderColor: "var(--color-border)" }}
    >
      <span className="text-xs font-medium theme-text-muted uppercase tracking-wide">
        {label}
      </span>
      <span className="text-sm font-medium theme-text text-right ml-4">
        {value || "—"}
      </span>
    </div>
  );
}
