// src/components/CreateTicketModal.jsx
import { useState } from "react";
import { X, AlertCircle } from "lucide-react";
import { useLanguage } from "../context/LanguageContext";
import { createTicket } from "../services/supportService";
import { useNavigate } from "react-router-dom";

const PRIORITY_OPTIONS = [
  { value: "low", label: "common.priorityLow" },
  { value: "medium", label: "common.priorityMedium" },
  { value: "high", label: "common.priorityHigh" },
  { value: "urgent", label: "common.priorityUrgent" },
];

export default function CreateTicketModal({ isOpen, onClose }) {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("medium");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!title.trim() || !description.trim()) {
      setError(t("common.titleAndDescriptionRequired"));
      return;
    }

    setIsSubmitting(true);
    setError("");
    try {
      const result = await createTicket(
        title.trim(),
        description.trim(),
        priority,
      );
      if (result.status === "success") {
        const ticketId = result.data.ticket.ticket_id;
        onClose();
        navigate(`/support/${ticketId}`);
      } else {
        setError(result.data || t("common.failedToCreateTicket"));
      }
    } catch (err) {
      console.error("CreateTicketModal error:", err);
      setError(t("common.failedToCreateTicket"));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isSubmitting) onClose();
      }}
    >
      <div
        className="relative w-full max-w-md rounded-2xl p-6 shadow-xl"
        style={{
          backgroundColor: "var(--color-shell)",
          border: "1px solid var(--color-border)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close button */}
        <button
          onClick={() => !isSubmitting && onClose()}
          className="absolute right-4 top-4 rounded-full p-1.5 transition hover:bg-[var(--color-soft)]"
          style={{ color: "var(--color-text-muted)" }}
          disabled={isSubmitting}
        >
          <X size={20} />
        </button>

        <h3 className="text-xl font-semibold theme-text mb-2">
          {t("common.newTicket")}
        </h3>
        <p className="text-sm theme-text-muted mb-6">
          {t("common.newTicketDescription")}
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Title */}
          <div>
            <label className="block text-xs font-bold uppercase tracking-wider theme-text-muted mb-1.5">
              {t("common.title")} <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("common.enterTicketTitle")}
              className="w-full rounded-xl border px-4 py-2.5 text-sm outline-none transition focus:ring-2"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-input)",
                color: "var(--color-text)",
              }}
              disabled={isSubmitting}
              required
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-bold uppercase tracking-wider theme-text-muted mb-1.5">
              {t("common.description")} <span className="text-red-500">*</span>
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("common.describeIssue")}
              rows={4}
              className="w-full rounded-xl border px-4 py-2.5 text-sm outline-none transition focus:ring-2"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-input)",
                color: "var(--color-text)",
              }}
              disabled={isSubmitting}
              required
            />
          </div>

          {/* Priority */}
          <div>
            <label className="block text-xs font-bold uppercase tracking-wider theme-text-muted mb-1.5">
              {t("common.priority")}
            </label>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              className="w-full rounded-xl border px-4 py-2.5 text-sm outline-none transition focus:ring-2"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-input)",
                color: "var(--color-text)",
              }}
              disabled={isSubmitting}
            >
              {PRIORITY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {t(opt.label)}
                </option>
              ))}
            </select>
          </div>

          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 text-sm text-red-500">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={() => !isSubmitting && onClose()}
              className="flex-1 rounded-xl border px-4 py-2.5 text-sm font-medium transition hover:bg-[var(--color-soft)]"
              style={{
                borderColor: "var(--color-border)",
                color: "var(--color-text-muted)",
              }}
              disabled={isSubmitting}
            >
              {t("common.cancel")}
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="flex-1 rounded-xl px-4 py-2.5 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              style={{ backgroundColor: "var(--color-accent)" }}
            >
              {isSubmitting ? t("common.creating") : t("common.createTicket")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
