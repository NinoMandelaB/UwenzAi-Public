// src/components/TicketMessage.jsx
import { useState } from "react";
import { User, Calendar, Reply, Lock } from "lucide-react";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import TicketReplyForm from "./TicketReplyForm";

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

export default function TicketMessage({
  message,
  ticketId,
  onReply,
  isAdmin = false,
  depth = 0,
}) {
  const { t } = useLanguage();
  const { user } = useAuth();
  const [showReplyForm, setShowReplyForm] = useState(false);

  const {
    message_id,
    sender,
    content,
    created_at,
    is_internal = false,
    replies = [],
  } = message;

  const senderName = sender?.display_name || sender?.username || "Unknown";
  const isSenderAdmin = sender?.is_admin || false;
  const isOwnMessage = user?.user_id === sender?.user_id;

  const indentClass = depth > 0 ? `ml-${Math.min(depth, 4) * 4}` : "";
  const borderColor = is_internal
    ? "border-amber-500/20"
    : "border-[var(--color-border)]";

  const handleReplySubmit = (content, parentId) => {
    if (onReply) {
      onReply(content, parentId);
    }
    setShowReplyForm(false);
  };

  return (
    <div className={`${indentClass} ${depth > 0 ? "mt-2" : ""}`}>
      {/* Message container – no card, just a row with avatar + content */}
      <div
        className={`flex items-start gap-3 py-3 border-b ${borderColor}`}
        style={{
          borderColor: is_internal
            ? "rgba(251,191,36,0.15)"
            : "var(--color-border)",
        }}
      >
        {/* Avatar */}
        <div
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold uppercase"
          style={{
            backgroundColor: isSenderAdmin
              ? "var(--color-accent-soft)"
              : "var(--color-soft)",
            color: isSenderAdmin
              ? "var(--color-accent)"
              : "var(--color-text-muted)",
          }}
        >
          {senderName.slice(0, 2)}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold theme-text">
              {senderName}
            </span>
            {isSenderAdmin && (
              <span
                className="rounded-full px-2 py-0.5 text-[9px] font-bold uppercase"
                style={{
                  backgroundColor: "var(--color-accent-soft)",
                  color: "var(--color-accent)",
                }}
              >
                {t("common.admin")}
              </span>
            )}
            {is_internal && (
              <span
                className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[9px] font-bold uppercase"
                style={{
                  backgroundColor: "rgba(251,191,36,0.15)",
                  color: "#f59e0b",
                }}
              >
                <Lock size={10} />
                {t("common.internal")}
              </span>
            )}
            {isOwnMessage && (
              <span className="text-[9px] font-medium text-[var(--color-text-muted)]">
                ({t("common.you")})
              </span>
            )}
            <span className="text-[10px] text-[var(--color-text-muted)]">
              {formatRelativeTime(created_at, t)}
            </span>
          </div>

          <div className="mt-1 text-sm theme-text whitespace-pre-wrap break-words">
            {content}
          </div>

          {/* Reply button – only for others, not internal */}
          {!is_internal && !isOwnMessage && (
            <button
              onClick={() => setShowReplyForm((prev) => !prev)}
              className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium transition hover:opacity-80"
              style={{ color: "var(--color-text-muted)" }}
            >
              <Reply size={12} />
              {t("common.reply")}
            </button>
          )}

          {showReplyForm && (
            <div className="mt-2">
              <TicketReplyForm
                ticketId={ticketId}
                parentMessageId={message_id}
                onSubmit={handleReplySubmit}
                onCancel={() => setShowReplyForm(false)}
                isAdmin={isAdmin}
                placeholder={t("common.replyToMessage")}
              />
            </div>
          )}
        </div>
      </div>

      {/* Replies (recursive) */}
      {replies && replies.length > 0 && (
        <div className="mt-1 space-y-1">
          {replies.map((reply) => (
            <TicketMessage
              key={reply.message_id}
              message={reply}
              ticketId={ticketId}
              onReply={onReply}
              isAdmin={isAdmin}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}
