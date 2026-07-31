// src/components/TicketStatusDropdown.jsx
import { useState } from "react";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import { updateTicketStatus } from "../services/supportService";

const STATUS_OPTIONS = [
  { value: "open", label: "common.open" },
  { value: "in_progress", label: "common.inProgress" },
  { value: "resolved", label: "common.resolved" },
  { value: "closed", label: "common.closed" },
];

export default function TicketStatusDropdown({
  ticketId,
  currentStatus,
  onStatusChange,
}) {
  const { t } = useLanguage();
  const { isAdmin } = useAuth();
  const [status, setStatus] = useState(currentStatus || "open");
  const [isUpdating, setIsUpdating] = useState(false);

  // Only admins can change status
  if (!isAdmin) return null;

  const handleChange = async (e) => {
    const newStatus = e.target.value;
    setIsUpdating(true);
    try {
      await updateTicketStatus(ticketId, newStatus);
      setStatus(newStatus);
      if (onStatusChange) onStatusChange(newStatus);
    } catch (error) {
      console.error("Failed to update status:", error);
      // Revert on error
      setStatus(currentStatus);
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium theme-text-muted">
        {t("common.status")}:
      </span>
      <select
        value={status}
        onChange={handleChange}
        disabled={isUpdating}
        className="rounded-lg border px-3 py-1.5 text-xs font-medium outline-none transition focus:ring-2"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-input)",
          color: "var(--color-text)",
        }}
      >
        {STATUS_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {t(option.label)}
          </option>
        ))}
      </select>
      {isUpdating && (
        <span className="text-xs text-[var(--color-text-muted)]">
          {t("common.updating")}
        </span>
      )}
    </div>
  );
}
