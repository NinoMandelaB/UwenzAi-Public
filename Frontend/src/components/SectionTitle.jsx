export default function SectionTitle({ children, action }) {
  return (
    <div className="flex items-center justify-between">
      <h3 className="text-sm font-semibold uppercase tracking-wide theme-text-muted">
        {children}
      </h3>

      {action ? action : null}
    </div>
  );
}
