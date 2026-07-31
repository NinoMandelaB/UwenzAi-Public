// src/components/FeedbackButton.jsx

import { useState } from "react";
import { MessageSquare, X, Star, CheckCircle } from "lucide-react";
import { submitFeedback } from "../services/feedbackService";

export default function FeedbackButton({
  open: controlledOpen,
  onClose: controlledOnClose,
}) {
  const [internalOpen, setInternalOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [rating, setRating] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");

  const isControlled = controlledOpen !== undefined;
  const isOpen = isControlled ? controlledOpen : internalOpen;
  const close = isControlled ? controlledOnClose : () => setInternalOpen(false);

  const resetForm = () => {
    setMessage("");
    setRating(0);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!message.trim()) {
      setError("Please enter a message.");
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
      setTimeout(() => {
        close();
        setSubmitted(false);
        resetForm();
      }, 2000);
    } catch (err) {
      if (err.code === "rate_limit_exceeded") {
        setError(
          "You've sent too many messages. Please wait a while before trying again.",
        );
      } else {
        setError(err.message || "Failed to send feedback. Please try again.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <>
      {/* Floating trigger – only show if not externally controlled */}
      {!isControlled && (
        <button
          onClick={() => setInternalOpen(true)}
          className="fixed bottom-20 right-4 sm:right-6 z-40 flex h-12 w-12 items-center justify-center rounded-full shadow-lg transition hover:scale-105 md:bottom-24"
          style={{ backgroundColor: "var(--color-accent)", color: "#082f49" }}
          aria-label="Send feedback"
        >
          <MessageSquare size={20} />
        </button>
      )}

      {/* Modal */}
      {isOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
          onClick={() => close()}
        >
          <div
            className="w-full max-w-md rounded-2xl border p-6 shadow-xl theme-card"
            style={{ borderColor: "var(--color-border)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold theme-text">
                Send Feedback
              </h2>
              <button
                onClick={() => {
                  close();
                  resetForm();
                }}
                className="rounded-full p-1 theme-text-muted hover:bg-white/5"
              >
                <X size={20} />
              </button>
            </div>

            {submitted ? (
              <div className="flex flex-col items-center gap-3 py-6">
                <CheckCircle size={48} className="text-emerald-400" />
                <p className="text-center theme-text">
                  Thank you for your feedback!
                </p>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="mb-1 block text-sm font-medium theme-text">
                    Rating (optional)
                  </label>
                  <div className="flex gap-1">
                    {[1, 2, 3, 4, 5].map((r) => (
                      <button
                        type="button"
                        key={r}
                        onClick={() => setRating(r)}
                        className="focus:outline-none"
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
                    Message <span className="text-red-400">*</span>
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
                    placeholder="Tell us what you think..."
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
                  {isSubmitting ? "Sending..." : "Submit Feedback"}
                </button>
              </form>
            )}
          </div>
        </div>
      )}
    </>
  );
}
