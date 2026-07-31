export default function PageCard({ children, className = "" }) {
  return (
    <section
      className={`rounded-2xl border theme-card p-4 shadow-sm ${className}`}
    >
      {children}
    </section>
  );
}
