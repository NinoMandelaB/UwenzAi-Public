// src/components/TicketReplyForm.jsx
import { useState } from "react";
import { Send, X } from "lucide-react";
import { useLanguage } from "../context/LanguageContext";

export default function TicketReplyForm({
  parentMessageId = null,
  onSubmit,
  onCancel,
  isAdmin = false,
  placeholder = "Write a reply...",
}) {
  const { t } = useLanguage();
  const [content, setContent] = useState("");
  const [isInternal, setIsInternal] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!content.trim()) return;

    setIsSubmitting(true);
    try {
      await onSubmit(content, parentMessageId, isInternal);
      setContent("");
      setIsInternal(false);
      if (onCancel) onCancel();
    } catch (error) {
      console.error("Failed to send reply:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder={placeholder}
        rows={3}
        className="w-full rounded-xl border p-3 text-sm outline-none transition focus:ring-2"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-input)",
          color: "var(--color-text)",
        }}
        disabled={isSubmitting}
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-4">
          {isAdmin && (
            <label className="flex items-center gap-2 text-xs theme-text-muted">
              <input
                type="checkbox"
                checked={isInternal}
                onChange={(e) => setIsInternal(e.target.checked)}
                className="rounded border-gray-400 accent-[var(--color-accent)]"
              />
              {t("common.internalNote")}
            </label>
          )}
        </div>

        <div className="flex gap-2">
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-xl px-4 py-2 text-sm font-medium transition"
              style={{
                color: "var(--color-text-muted)",
                backgroundColor: "var(--color-soft)",
              }}
              disabled={isSubmitting}
            >
              <X size={16} className="inline mr-1" />
              {t("common.cancel")}
            </button>
          )}
          <button
            type="submit"
            disabled={isSubmitting || !content.trim()}
            className="rounded-xl px-4 py-2 text-sm font-medium text-white transition disabled:opacity-50"
            style={{
              backgroundColor: "var(--color-accent)",
            }}
          >
            <Send size={16} className="inline mr-1" />
            {isSubmitting ? t("common.sending") : t("common.send")}
          </button>
        </div>
      </div>
    </form>
  );
}
