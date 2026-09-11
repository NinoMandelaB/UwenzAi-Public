// src/components/FeedbackForm.jsx

import { useState } from "react";
import { Star, CheckCircle } from "lucide-react";
import { submitFeedback } from "../services/feedbackService";
import { useLanguage } from "../context/LanguageContext";

/**
 * Inline feedback form (message + optional 1-5 star rating).
 * Works for authenticated users and guests – the backend accepts
 * anonymous feedback (AllowAny) with IP-based rate limiting.
 */
export default function FeedbackForm() {
  const { t } = useLanguage();
  const [message, setMessage] = useState("");
  const [rating, setRating] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!message.trim()) {
      setError(t("contact.messageRequired"));
      return;
    }
    setIsSubmitting(true);
    setError("");
    try {
      await submitFeedback({
        message: message.trim(),
        rating: rating || null,
      });
      setSubmitted(true);
      setMessage("");
      setRating(0);
      setTimeout(() => setSubmitted(false), 5000);
    } catch (err) {
      if (err.code === "rate_limit_exceeded") {
        setError(t("contact.rateLimited"));
      } else {
        setError(err.message || t("contact.sendFailed"));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div className="flex flex-col items-center gap-3 py-6">
        <CheckCircle size={48} className="text-emerald-400" />
        <p className="text-center theme-text">{t("contact.submitted")}</p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="mb-1 block text-sm font-medium theme-text">
          {t("contact.rating")}
        </label>
        <div className="flex gap-1">
          {[1, 2, 3, 4, 5].map((r) => (
            <button
              type="button"
              key={r}
              onClick={() => setRating(r)}
              className="focus:outline-none"
              aria-label={`${r}`}
            >
              <Star
                size={28}
                fill={r <= rating ? "#fbbf24" : "none"}
                color="#fbbf24"
              />
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium theme-text">
          {t("contact.message")} <span className="text-red-400">*</span>
        </label>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={4}
          required
          className="w-full rounded-xl border p-3 text-sm outline-none transition focus:border-cyan-400/50"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-card)",
            color: "var(--color-text)",
          }}
          placeholder={t("contact.messagePlaceholder")}
          maxLength={2000}
        />
        <p className="mt-1 text-right text-xs theme-text-muted">
          {message.length}/2000
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <button
        type="submit"
        disabled={isSubmitting}
        className="w-full rounded-xl py-2.5 text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
        style={{
          backgroundColor: "var(--color-accent)",
          color: "#082f49",
        }}
      >
        {isSubmitting ? t("contact.submitting") : t("contact.submit")}
      </button>
    </form>
  );
}
