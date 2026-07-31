// src/components/TicketAssigneeDropdown.jsx
import { useState, useEffect } from "react";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import { updateTicketStatus } from "../services/supportService";
import { listStaffUsers } from "../services/staffService";

export default function TicketAssigneeDropdown({
  ticketId,
  currentAssignee,
  onAssign,
}) {
  const { t } = useLanguage();
  const { isAdmin } = useAuth();
  const [staffUsers, setStaffUsers] = useState([]);
  const [assigneeId, setAssigneeId] = useState(currentAssignee?.user_id || "");
  const [isLoading, setIsLoading] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState("");

  // Only admins can assign tickets
  if (!isAdmin) return null;

  // Fetch staff users on mount
  useEffect(() => {
    async function fetchStaffUsers() {
      setIsLoading(true);
      setError("");
      try {
        const data = await listStaffUsers();
        setStaffUsers(data.data.users || []);
      } catch (err) {
        console.error("Failed to fetch staff users:", err);
        setError(t("common.failedToLoadUsers"));
      } finally {
        setIsLoading(false);
      }
    }
    fetchStaffUsers();
  }, [t]);

  // Update when currentAssignee changes
  useEffect(() => {
    setAssigneeId(currentAssignee?.user_id || "");
  }, [currentAssignee]);

  const handleChange = async (e) => {
    const newAssigneeId = e.target.value;
    setIsUpdating(true);
    setError("");
    try {
      // Explicitly send "" for unassign, not null
      const assignedId = newAssigneeId || "";
      await updateTicketStatus(ticketId, undefined, assignedId);
      setAssigneeId(newAssigneeId);
      // Find the assigned user or null
      const assignedUser = newAssigneeId
        ? staffUsers.find((u) => u.user_id === newAssigneeId)
        : null;
      if (onAssign) onAssign(assignedUser);
    } catch (error) {
      console.error("Failed to assign ticket:", error);
      setError(t("common.failedToAssign"));
      setAssigneeId(currentAssignee?.user_id || "");
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium theme-text-muted">
        {t("common.assignTo")}:
      </span>
      <select
        value={assigneeId}
        onChange={handleChange}
        disabled={isLoading || isUpdating}
        className="rounded-lg border px-3 py-1.5 text-xs font-medium outline-none transition focus:ring-2"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-input)",
          color: "var(--color-text)",
        }}
      >
        <option value="">{t("common.unassigned")}</option>
        {isLoading ? (
          <option disabled>{t("common.loading")}</option>
        ) : (
          staffUsers.map((user) => (
            <option key={user.user_id} value={user.user_id}>
              {user.display_name || user.username}
            </option>
          ))
        )}
      </select>
      {isUpdating && (
        <span className="text-xs text-[var(--color-text-muted)]">
          {t("common.updating")}
        </span>
      )}
      {error && <span className="text-xs text-red-500">{error}</span>}
    </div>
  );
}
