import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import {
  listFeedback,
  markFeedbackAsRead,
  deleteFeedback,
} from "../../services/feedbackService";
import { Loader2, Star, User, MailOpen, Trash2 } from "lucide-react";
import PageCard from "../../components/PageCard";
import ConfirmDialog from "../../components/ConfirmDialog";

export default function AdminFeedback() {
  const { isAdmin } = useAuth();
  const [feedback, setFeedback] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Delete state
  const [feedbackToDelete, setFeedbackToDelete] = useState(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isConfirmOpen, setIsConfirmOpen] = useState(false);

  useEffect(() => {
    if (!isAdmin) return;
    fetchFeedback();
  }, [isAdmin]);

  const fetchFeedback = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await listFeedback();
      setFeedback(res?.data?.feedback || []);
    } catch (err) {
      setError(err.message || "Failed to load feedback.");
    } finally {
      setLoading(false);
    }
  };

  const handleMarkRead = async (feedbackId) => {
    try {
      await markFeedbackAsRead(feedbackId);
      setFeedback((prev) =>
        prev.map((f) =>
          f.feedback_id === feedbackId ? { ...f, is_read: true } : f,
        ),
      );
    } catch (err) {
      console.error("Failed to mark as read:", err);
    }
  };

  // Delete handlers
  const handleDeleteClick = (item) => {
    setFeedbackToDelete(item);
    setIsConfirmOpen(true);
  };

  const handleCancelDelete = () => {
    setIsConfirmOpen(false);
    setFeedbackToDelete(null);
  };

  const handleConfirmDelete = async () => {
    if (!feedbackToDelete) return;
    setIsDeleting(true);
    try {
      await deleteFeedback(feedbackToDelete.feedback_id);
      // Remove from local list
      setFeedback((prev) =>
        prev.filter((f) => f.feedback_id !== feedbackToDelete.feedback_id),
      );
    } catch (err) {
      console.error("Failed to delete feedback:", err);
      setError(err.message || "Failed to delete feedback.");
    } finally {
      setIsDeleting(false);
      setIsConfirmOpen(false);
      setFeedbackToDelete(null);
    }
  };

  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold theme-text">User Feedback</h1>
        <p className="mt-1 text-sm theme-text-muted">
          Review and manage feedback submissions from users.
        </p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="animate-spin text-cyan-400" size={32} />
        </div>
      ) : error ? (
        <PageCard className="text-center">
          <p className="text-red-400">{error}</p>
        </PageCard>
      ) : feedback.length === 0 ? (
        <PageCard className="text-center">
          <p className="theme-text-muted">No feedback submissions yet.</p>
        </PageCard>
      ) : (
        <div className="space-y-3">
          {feedback.map((item) => (
            <div
              key={item.feedback_id}
              className={`rounded-xl border p-4 transition ${
                !item.is_read ? "border-l-4 border-l-cyan-400" : "opacity-75"
              }`}
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-card)",
              }}
            >
              <div className="mb-2 flex items-start justify-between">
                <div className="flex items-center gap-2">
                  <User size={16} className="theme-text-muted" />
                  <span className="font-medium theme-text">{item.user}</span>
                  {!item.is_read && (
                    <span className="rounded-full bg-cyan-500/20 px-2 py-0.5 text-[10px] font-medium text-cyan-300">
                      NEW
                    </span>
                  )}
                </div>
                <span className="text-xs theme-text-muted">
                  {new Date(item.created_at).toLocaleString()}
                </span>
              </div>

              {item.rating && (
                <div className="mb-2 flex gap-0.5">
                  {[1, 2, 3, 4, 5].map((r) => (
                    <Star
                      key={r}
                      size={14}
                      fill={r <= item.rating ? "#fbbf24" : "none"}
                      color="#fbbf24"
                    />
                  ))}
                </div>
              )}

              <p className="whitespace-pre-wrap text-sm theme-text">
                {item.message}
              </p>

              <div className="mt-3 flex justify-end gap-2">
                {!item.is_read && (
                  <button
                    onClick={() => handleMarkRead(item.feedback_id)}
                    className="flex items-center gap-1 rounded-lg px-3 py-1 text-xs font-medium text-cyan-400 transition hover:bg-cyan-400/10"
                  >
                    <MailOpen size={12} />
                    Mark as read
                  </button>
                )}
                <button
                  onClick={() => handleDeleteClick(item)}
                  className="flex items-center gap-1 rounded-lg px-3 py-1 text-xs font-medium text-red-400 transition hover:bg-red-400/10"
                >
                  <Trash2 size={12} />
                  Delete
                </button>
              </div>

              {item.is_read && item.read_by && (
                <p className="mt-2 text-right text-[10px] theme-text-muted">
                  Read by {item.read_by} on{" "}
                  {item.read_at ? new Date(item.read_at).toLocaleString() : ""}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Confirm Delete Dialog */}
      <ConfirmDialog
        isOpen={isConfirmOpen}
        title="Delete Feedback"
        message={`Are you sure you want to delete this feedback from ${feedbackToDelete?.user}? This action cannot be undone.`}
        confirmText="Delete"
        isLoading={isDeleting}
        onConfirm={handleConfirmDelete}
        onClose={handleCancelDelete}
      />
    </div>
  );
}
