// src/pages/SupportDetail.jsx
import { useState, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ChevronLeft, Loader2 } from "lucide-react";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import {
  getTicket,
  replyToTicket,
  markTicketRead,
} from "../services/supportService";
import TicketStatusBadge from "../components/TicketStatusBadge";
import TicketPriorityBadge from "../components/TicketPriorityBadge";
import TicketMessage from "../components/TicketMessage";
import TicketReplyForm from "../components/TicketReplyForm";
import TicketStatusDropdown from "../components/TicketStatusDropdown";
import TicketAssigneeDropdown from "../components/TicketAssigneeDropdown";
import PageCard from "../components/PageCard";

export default function SupportDetail() {
  const { ticketId } = useParams();
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { isAdmin } = useAuth();
  const messagesEndRef = useRef(null);

  const [ticket, setTicket] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  const fetchTicket = async () => {
    setIsLoading(true);
    setError("");
    try {
      const result = await getTicket(ticketId);
      if (result.status === "success") {
        setTicket(result.data.ticket);
        await markTicketRead(ticketId);
      } else {
        setError(result.data || t("common.failedToLoadTicket"));
      }
    } catch (err) {
      setError(t("common.failedToLoadTicket"));
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchTicket();
  }, [ticketId]);

  useEffect(() => {
    if (!isLoading && ticket) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [ticket, isLoading]);

  const handleReply = async (
    content,
    parentMessageId = null,
    isInternal = false,
  ) => {
    setIsSubmitting(true);
    try {
      await replyToTicket(ticketId, content, parentMessageId, isInternal);
      await fetchTicket();
    } catch (err) {
      console.error("Failed to send reply:", err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleStatusChange = () => fetchTicket();
  const handleAssign = () => fetchTicket();

  if (isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-[var(--color-accent)]" />
      </div>
    );
  }

  if (error || !ticket) {
    return (
      <PageCard className="text-center">
        <p className="text-sm text-red-500">
          {error || t("common.ticketNotFound")}
        </p>
        <button
          onClick={() => navigate("/support")}
          className="mt-2 text-sm font-medium underline"
          style={{ color: "var(--color-accent)" }}
        >
          {t("common.backToSupport")}
        </button>
      </PageCard>
    );
  }

  const {
    title,
    status,
    priority,
    created_by,
    created_at,
    messages = [],
  } = ticket;
  const rootMessages = messages.filter((msg) => !msg.parent_id);

  // Shortened ticket ID (first 8 characters)
  const shortId = ticketId.slice(0, 8);

  return (
    <div className="space-y-6">
      {/* Back button */}
      <button
        onClick={() => navigate("/support")}
        className="inline-flex items-center gap-2 text-sm font-medium transition hover:opacity-80"
        style={{ color: "var(--color-text-muted)" }}
      >
        <ChevronLeft size={18} />
        {t("common.backToSupport")}
      </button>

      {/* Ticket Header */}
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-xl font-semibold theme-text">{title}</h2>
          <span
            className="shrink-0 font-mono text-xs font-medium theme-text-muted"
            title={`Ticket ID: ${ticketId}`}
          >
            #{shortId}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <TicketStatusBadge status={status} />
          <TicketPriorityBadge priority={priority} />
          <span className="text-xs theme-text-muted">
            {t("common.createdBy")}:{" "}
            {created_by?.display_name || created_by?.username || "Unknown"}
          </span>
          <span className="text-xs theme-text-muted">
            {new Date(created_at).toLocaleDateString()}
          </span>
        </div>

        {isAdmin && (
          <div className="mt-3 flex flex-wrap items-center gap-4">
            <TicketStatusDropdown
              ticketId={ticketId}
              currentStatus={status}
              onStatusChange={handleStatusChange}
            />
            <TicketAssigneeDropdown
              ticketId={ticketId}
              currentAssignee={ticket.assigned_to}
              onAssign={handleAssign}
            />
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="space-y-3">
        {rootMessages.length === 0 ? (
          <div className="text-center py-8">
            <p className="text-sm theme-text-muted">
              {t("common.noMessagesYet")}
            </p>
          </div>
        ) : (
          rootMessages.map((msg) => (
            <TicketMessage
              key={msg.message_id}
              message={msg}
              ticketId={ticketId}
              onReply={handleReply}
              isAdmin={isAdmin}
              depth={0}
            />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Reply Form */}
      <div className="pt-4">
        <TicketReplyForm
          ticketId={ticketId}
          onSubmit={handleReply}
          isAdmin={isAdmin}
          placeholder={t("common.typeYourReply")}
        />
      </div>
    </div>
  );
}
