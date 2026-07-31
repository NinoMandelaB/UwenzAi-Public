export default function StatCard({ label, value }) {
  return (
    <div className="rounded-2xl border theme-card p-4">
      <p className="text-xs theme-text-muted">{label}</p>
      <p className="mt-2 text-lg font-semibold theme-text">{value}</p>
    </div>
  );
}
