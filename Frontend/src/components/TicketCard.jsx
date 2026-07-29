// src/components/TicketCard.jsx
import { useNavigate } from "react-router-dom";
import { MessageCircle, User, Calendar, Users } from "lucide-react";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import TicketStatusBadge from "./TicketStatusBadge";
import TicketPriorityBadge from "./TicketPriorityBadge";

function formatRelativeTime(dateString, t) {
  if (!dateString) return "";
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now - date;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return t("common.justNow");
  if (mins < 60) {
    const template = t("common.minAgo");
    return template.replace("{count}", mins);
  }
  const hours = Math.floor(mins / 60);
  if (hours < 24) {
    const template = t("common.hrAgo");
    return template.replace("{count}", hours);
  }
  return date.toLocaleDateString();
}

export default function TicketCard({ ticket, onClick }) {
  const { t } = useLanguage();
  const { isAdmin } = useAuth();
  const navigate = useNavigate();

  const {
    ticket_id,
    title,
    status,
    priority,
    created_at,
    created_by,
    assigned_to,
    message_count = 0,
    unread_count = 0,
  } = ticket;

  const createdByName =
    created_by?.display_name || created_by?.username || "Unknown";
  const assignedToName =
    assigned_to?.display_name || assigned_to?.username || "Unassigned";

  const handleClick = () => {
    if (onClick) {
      onClick(ticket_id, unread_count);
    } else {
      navigate(`/support/${ticket_id}`);
    }
  };

  // Shortened ticket ID (first 8 characters)
  const shortId = ticket_id.slice(0, 8);

  return (
    <div
      onClick={handleClick}
      className="group cursor-pointer rounded-2xl border p-4 transition-all hover:shadow-md active:scale-[0.99]"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-card)",
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-semibold theme-text truncate">
              {title}
            </h4>
            <span
              className="shrink-0 font-mono text-[10px] font-medium theme-text-muted"
              title={`Ticket ID: ${ticket_id}`}
            >
              #{shortId}
            </span>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <TicketStatusBadge status={status} />
            <TicketPriorityBadge priority={priority} />
            {unread_count > 0 && (
              <span className="inline-flex items-center rounded-full bg-red-500 px-2 py-0.5 text-[10px] font-bold text-white animate-pulse">
                New
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1.5 text-xs theme-text-muted shrink-0">
          <MessageCircle size={14} />
          <span>
            {message_count}{" "}
            {message_count === 1 ? t("common.reply") : t("common.replies")}
          </span>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs theme-text-muted">
        <span className="flex items-center gap-1">
          <User size={13} />
          {createdByName}
        </span>
        <span className="flex items-center gap-1">
          <Calendar size={13} />
          {formatRelativeTime(created_at, t)}
        </span>
        {isAdmin && assigned_to && (
          <span className="flex items-center gap-1">
            <Users size={13} />
            {t("common.assignedTo")}: {assignedToName}
          </span>
        )}
      </div>
    </div>
  );
}
