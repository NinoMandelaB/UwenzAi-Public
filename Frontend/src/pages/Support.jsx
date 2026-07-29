// src/pages/Support.jsx
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Search, Loader2 } from "lucide-react";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import { listTickets, markTicketRead } from "../services/supportService";
import TicketCard from "../components/TicketCard";
import CreateTicketModal from "../components/CreateTicketModal";
import PageCard from "../components/PageCard";

const STATUS_OPTIONS = [
  { value: "", labelKey: "common.allStatuses" },
  { value: "open", labelKey: "common.open" },
  { value: "in_progress", labelKey: "common.inProgress" },
  { value: "resolved", labelKey: "common.resolved" },
  { value: "closed", labelKey: "common.closed" },
];

const PRIORITY_OPTIONS = [
  { value: "", labelKey: "common.allPriorities" },
  { value: "low", labelKey: "common.priorityLow" },
  { value: "medium", labelKey: "common.priorityMedium" },
  { value: "high", labelKey: "common.priorityHigh" },
  { value: "urgent", labelKey: "common.priorityUrgent" },
];

export default function Support() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const { isAuthenticated, isAdmin } = useAuth();

  const [tickets, setTickets] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);

  // Filters
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [searchTerm, setSearchTerm] = useState("");

  // Fetch tickets on mount and when filters change
  useEffect(() => {
    if (!isAuthenticated) return;
    fetchTickets();
  }, [statusFilter, priorityFilter]);

  const fetchTickets = async () => {
    setIsLoading(true);
    setError("");
    try {
      const filters = {};
      if (statusFilter) filters.status = statusFilter;
      if (priorityFilter) filters.priority = priorityFilter;
      const result = await listTickets(filters);
      if (result.status === "success") {
        setTickets(result.data.tickets || []);
      } else {
        setError(result.data || t("common.failedToLoadTickets"));
      }
    } catch (err) {
      setError(t("common.failedToLoadTickets"));
    } finally {
      setIsLoading(false);
    }
  };

  // Client-side search filtering
  const filteredTickets = tickets.filter((ticket) => {
    if (!searchTerm.trim()) return true;
    const term = searchTerm.toLowerCase().trim();
    return (
      ticket.title?.toLowerCase().includes(term) ||
      ticket.description?.toLowerCase().includes(term) ||
      ticket.created_by?.display_name?.toLowerCase().includes(term) ||
      ticket.created_by?.username?.toLowerCase().includes(term)
    );
  });

  const handleTicketClick = async (ticketId, unreadCount) => {
    // Mark ticket as read if there are unread messages
    if (unreadCount > 0) {
      try {
        await markTicketRead(ticketId);
        // Update local state to remove unread count for this ticket
        setTickets((prev) =>
          prev.map((t) =>
            t.ticket_id === ticketId ? { ...t, unread_count: 0 } : t,
          ),
        );
        // Dispatch custom event to refresh header unread count
        window.dispatchEvent(new CustomEvent("support-unread-updated"));
      } catch (err) {
        console.error("Failed to mark ticket as read:", err);
      }
    }
    navigate(`/support/${ticketId}`);
  };

  if (!isAuthenticated) {
    navigate("/login");
    return null;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <section className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold theme-text">
            {t("common.support")}
          </h2>
          <p className="mt-1 text-sm theme-text-muted">
            {t("common.supportDescription")}
          </p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="shrink-0 rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90"
          style={{
            backgroundColor: "var(--color-accent-soft)",
            color: "var(--color-accent)",
            border: "1px solid var(--color-accent-soft)",
          }}
        >
          <Plus size={16} className="inline mr-1.5" />
          {t("common.newTicket")}
        </button>
      </section>

      {/* Search + Filters */}
      <section className="space-y-3">
        <div
          className="rounded-2xl border p-3"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-card)",
          }}
        >
          <div className="flex items-center gap-2">
            <Search size={16} className="text-[var(--color-text-muted)]" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder={t("common.searchTickets")}
              className="w-full bg-transparent text-sm outline-none"
              style={{ color: "var(--color-text)" }}
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-full border px-3 py-2 text-xs font-medium outline-none transition focus:ring-2"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-soft)",
              color: statusFilter
                ? "var(--color-text)"
                : "var(--color-text-muted)",
            }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {t(opt.labelKey)}
              </option>
            ))}
          </select>

          <select
            value={priorityFilter}
            onChange={(e) => setPriorityFilter(e.target.value)}
            className="rounded-full border px-3 py-2 text-xs font-medium outline-none transition focus:ring-2"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-soft)",
              color: priorityFilter
                ? "var(--color-text)"
                : "var(--color-text-muted)",
            }}
          >
            {PRIORITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {t(opt.labelKey)}
              </option>
            ))}
          </select>

          {(statusFilter || priorityFilter || searchTerm) && (
            <button
              onClick={() => {
                setStatusFilter("");
                setPriorityFilter("");
                setSearchTerm("");
              }}
              className="rounded-full px-3 py-2 text-xs font-medium transition hover:bg-[var(--color-soft)]"
              style={{ color: "var(--color-text-muted)" }}
            >
              {t("common.clearFilters")}
            </button>
          )}
        </div>
      </section>

      {/* Ticket List */}
      <section className="space-y-4">
        {isLoading ? (
          <PageCard className="text-center">
            <div className="py-8">
              <Loader2 className="mx-auto h-8 w-8 animate-spin text-[var(--color-accent)]" />
              <p className="mt-4 text-sm theme-text-muted">
                {t("common.loadingTickets")}
              </p>
            </div>
          </PageCard>
        ) : error ? (
          <PageCard className="text-center">
            <p className="text-sm text-red-500">{error}</p>
            <button
              onClick={fetchTickets}
              className="mt-2 text-sm font-medium underline"
              style={{ color: "var(--color-accent)" }}
            >
              {t("common.retry")}
            </button>
          </PageCard>
        ) : filteredTickets.length === 0 ? (
          <PageCard className="text-center">
            <div className="py-8">
              <p className="text-sm font-medium theme-text">
                {t("common.noTicketsFound")}
              </p>
              <p className="mt-1 text-sm theme-text-muted">
                {t("common.noTicketsDescription")}
              </p>
              {!statusFilter && !priorityFilter && !searchTerm && (
                <button
                  onClick={() => setShowCreateModal(true)}
                  className="mt-4 inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90"
                  style={{
                    backgroundColor: "var(--color-accent)",
                    color: "#082f49",
                  }}
                >
                  <Plus size={16} />
                  {t("common.createFirstTicket")}
                </button>
              )}
            </div>
          </PageCard>
        ) : (
          filteredTickets.map((ticket) => (
            <TicketCard
              key={ticket.ticket_id}
              ticket={ticket}
              onClick={handleTicketClick}
            />
          ))
        )}
      </section>

      {/* Create Ticket Modal */}
      <CreateTicketModal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
      />
    </div>
  );
}
