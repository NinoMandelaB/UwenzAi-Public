// src/components/TicketPriorityBadge.jsx
import { useLanguage } from "../context/LanguageContext";

const PRIORITY_CONFIG = {
  low: {
    label: "common.priorityLow",
    className: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  },
  medium: {
    label: "common.priorityMedium",
    className: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  },
  high: {
    label: "common.priorityHigh",
    className: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  },
  urgent: {
    label: "common.priorityUrgent",
    className: "bg-red-500/20 text-red-400 border-red-500/30",
  },
};

export default function TicketPriorityBadge({ priority, className = "" }) {
  const { t } = useLanguage();
  const config =
    PRIORITY_CONFIG[priority?.toLowerCase()] || PRIORITY_CONFIG.medium;
  const label = t(config.label) || priority?.toLowerCase() || "Medium";

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${config.className} ${className}`}
    >
      {label}
    </span>
  );
}
