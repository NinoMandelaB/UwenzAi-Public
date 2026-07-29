// src/components/TicketStatusBadge.jsx
import { useLanguage } from "../context/LanguageContext";

const STATUS_CONFIG = {
  open: {
    label: "common.open",
    className: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  },
  in_progress: {
    label: "common.inProgress",
    className: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  },
  resolved: {
    label: "common.resolved",
    className: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  },
  closed: {
    label: "common.closed",
    className: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  },
};

export default function TicketStatusBadge({ status, className = "" }) {
  const { t } = useLanguage();
  const config = STATUS_CONFIG[status?.toLowerCase()] || STATUS_CONFIG.open;
  const label = t(config.label) || status?.replace(/_/g, " ") || "Open";

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${config.className} ${className}`}
    >
      {label}
    </span>
  );
}
