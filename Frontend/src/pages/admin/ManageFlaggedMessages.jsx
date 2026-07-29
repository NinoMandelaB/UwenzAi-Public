// src/pages/admin/ManageFlaggedMessages.jsx
import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import PageCard from "../../components/PageCard";
import SectionTitle from "../../components/SectionTitle";
import { useLanguage } from "../../context/LanguageContext";
import { useAuth } from "../../context/AuthContext";
import {
  listMessageFlags,
  reviewMessageFlag,
} from "../../services/messageFlagService";
import ConfirmDialog from "../../components/ConfirmDialog";
import { Loader2, Flag, Filter } from "lucide-react";

const PAGE_SIZE = 20;

function formatDate(isoString) {
  if (!isoString) return "";
  return new Date(isoString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ManageFlaggedMessages() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { user, isAdmin } = useAuth();

  const [flags, setFlags] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [reviewModalOpen, setReviewModalOpen] = useState(false);
  const [selectedFlag, setSelectedFlag] = useState(null);
  const [resolutionNote, setResolutionNote] = useState("");
  const [submittingReview, setSubmittingReview] = useState(false);

  // Filter state – default "pending"
  const [filterStatus, setFilterStatus] = useState("pending");

  // Redirect if not admin
  useEffect(() => {
    if (!user) {
      navigate("/login");
    } else if (!isAdmin) {
      navigate("/characters");
    }
  }, [user, isAdmin, navigate]);

  const fetchFlags = useCallback(
    async (pageNum, status = filterStatus) => {
      setLoading(true);
      setError(null);
      try {
        const filters = {};
        if (status === "pending") filters.reviewed = false;
        else if (status === "reviewed") filters.reviewed = true;
        // "all" sends no filter

        const data = await listMessageFlags(pageNum, PAGE_SIZE, filters);
        setFlags(data.data.flags);
        setTotal(data.data.total);
        setHasNext(data.data.has_next);
        setPage(data.data.page);
      } catch (err) {
        setError(err.message || "Failed to load flagged messages");
      } finally {
        setLoading(false);
      }
    },
    [filterStatus],
  );

  useEffect(() => {
    if (user && isAdmin) {
      fetchFlags(1, filterStatus);
    }
  }, [user, isAdmin, filterStatus, fetchFlags]);

  const handleFilterChange = (newStatus) => {
    setFilterStatus(newStatus);
    setPage(1);
  };

  const handleReview = (flag) => {
    setSelectedFlag(flag);
    setResolutionNote(flag.resolution_note || "");
    setReviewModalOpen(true);
  };

  const handleSubmitReview = async () => {
    if (!selectedFlag) return;
    setSubmittingReview(true);
    try {
      await reviewMessageFlag(selectedFlag.flag_id, {
        resolution_note: resolutionNote,
        dismiss: false,
      });
      setFlags((prev) =>
        prev.map((f) =>
          f.flag_id === selectedFlag.flag_id
            ? { ...f, reviewed: true, resolution_note: resolutionNote }
            : f,
        ),
      );
      setReviewModalOpen(false);
      setSelectedFlag(null);
    } catch (err) {
      console.error("Failed to review flag", err);
    } finally {
      setSubmittingReview(false);
    }
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const pageNumbers = [];
  const maxVisible = 5;
  let startPage = Math.max(1, page - Math.floor(maxVisible / 2));
  let endPage = startPage + maxVisible - 1;
  if (endPage > totalPages) {
    endPage = totalPages;
    startPage = Math.max(1, endPage - maxVisible + 1);
  }
  for (let i = startPage; i <= endPage; i++) {
    pageNumbers.push(i);
  }

  if (!user || !isAdmin) return null;

  return (
    <div className="w-full space-y-6">
      {/* Page heading */}
      <div>
        <h1 className="text-2xl font-bold theme-text flex items-center gap-3">
          <Flag size={24} className="text-amber-400" />
          Flagged Messages
        </h1>
        <p className="mt-2 text-base theme-text-muted">
          Review messages that have been reported by users.
        </p>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-2">
        <Filter size={18} className="text-gray-500" />
        <select
          value={filterStatus}
          onChange={(e) => handleFilterChange(e.target.value)}
          className="rounded-lg border px-3 py-2 text-sm"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-soft)",
            color: "var(--color-text)",
          }}
        >
          <option value="pending">Pending</option>
          <option value="reviewed">Reviewed</option>
          <option value="all">All</option>
        </select>
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-3 py-20">
          <Loader2 className="animate-spin" size={24} />
          <span className="text-base theme-text-muted">
            {t("common.loading") || "Loading..."}
          </span>
        </div>
      ) : error ? (
        <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-6 text-center text-base text-red-300">
          {error}
        </div>
      ) : flags.length === 0 ? (
        <div className="py-20 text-center">
          <Flag size={40} className="mx-auto mb-4 text-gray-500" />
          <p className="text-base theme-text-muted">
            No flagged messages at this time.
          </p>
        </div>
      ) : (
        <>
          <div className="text-sm theme-text-muted">
            Showing {(page - 1) * PAGE_SIZE + 1}–
            {Math.min(page * PAGE_SIZE, total)} of {total} flags
          </div>

          {/* Mobile Card View */}
          <div className="sm:hidden space-y-4">
            {flags.map((flag) => (
              <div
                key={flag.flag_id}
                className="rounded-2xl border p-4 shadow-sm theme-card"
                style={{ borderColor: "var(--color-border)" }}
              >
                <div className="space-y-2 text-sm">
                  <div>
                    <span className="font-medium theme-text">Reason:</span>{" "}
                    <span className="theme-text-muted">{flag.reason}</span>
                  </div>
                  <div>
                    <span className="font-medium theme-text">Message:</span>
                    <p className="theme-text-muted mt-1 line-clamp-3">
                      "{flag.message_snippet}"
                    </p>
                  </div>
                  <div>
                    <span className="font-medium theme-text">Reported by:</span>{" "}
                    <span className="theme-text-muted">{flag.reported_by}</span>
                  </div>
                  <div>
                    <span className="font-medium theme-text">Date:</span>{" "}
                    <span className="theme-text-muted">
                      {formatDate(flag.created_at)}
                    </span>
                  </div>
                  <div>
                    <span className="font-medium theme-text">Status:</span>{" "}
                    {flag.reviewed ? (
                      <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-sm font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
                        Reviewed
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-sm font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                        Pending
                      </span>
                    )}
                  </div>
                  <div className="pt-2">
                    <button
                      onClick={() => handleReview(flag)}
                      className="w-full rounded-lg px-3 py-1.5 text-sm font-medium text-white transition-opacity"
                      style={{ backgroundColor: "var(--color-accent)" }}
                    >
                      {flag.reviewed ? "Edit Review" : "Review"}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Desktop Table */}
          <PageCard className="hidden sm:block">
            <div
              className="overflow-x-auto rounded-xl border"
              style={{ borderColor: "var(--color-border)" }}
            >
              <table className="min-w-full text-left text-sm">
                <thead
                  className="text-sm"
                  style={{
                    backgroundColor: "var(--color-soft)",
                    color: "var(--color-text-muted)",
                  }}
                >
                  <tr>
                    <th className="px-4 py-3 font-medium">Reason</th>
                    <th className="px-4 py-3 font-medium">Message Snippet</th>
                    <th className="px-4 py-3 font-medium hidden sm:table-cell">
                      Reported by
                    </th>
                    <th className="px-4 py-3 font-medium hidden sm:table-cell">
                      Date
                    </th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium text-right">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {flags.map((flag) => (
                    <tr
                      key={flag.flag_id}
                      className="transition-colors hover:bg-[var(--color-soft)]"
                      style={{ borderBottom: "1px solid var(--color-border)" }}
                    >
                      <td className="px-4 py-3">
                        <p className="font-medium theme-text line-clamp-2">
                          {flag.reason}
                        </p>
                      </td>
                      <td className="px-4 py-3">
                        <p className="text-sm theme-text-muted line-clamp-2">
                          {flag.message_snippet}
                        </p>
                      </td>
                      <td className="px-4 py-3 text-sm theme-text-muted hidden sm:table-cell">
                        {flag.reported_by}
                      </td>
                      <td className="px-4 py-3 text-sm theme-text-muted hidden sm:table-cell">
                        {formatDate(flag.created_at)}
                      </td>
                      <td className="px-4 py-3">
                        {flag.reviewed ? (
                          <span className="inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-sm font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
                            Reviewed
                          </span>
                        ) : (
                          <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-sm font-medium text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                            Pending
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => handleReview(flag)}
                          className="rounded-lg px-3 py-1.5 text-sm font-medium text-white transition-opacity"
                          style={{ backgroundColor: "var(--color-accent)" }}
                        >
                          {flag.reviewed ? "Edit Review" : "Review"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </PageCard>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-1 pt-2">
              <button
                onClick={() => fetchFlags(page - 1)}
                disabled={page === 1}
                className="rounded-lg px-3 py-1.5 text-xs font-medium transition"
                style={{
                  backgroundColor:
                    page === 1 ? "transparent" : "var(--color-soft)",
                  color:
                    page === 1
                      ? "var(--color-text-muted)"
                      : "var(--color-text)",
                  opacity: page === 1 ? 0.5 : 1,
                }}
              >
                Prev
              </button>
              {startPage > 1 && (
                <>
                  <button
                    onClick={() => fetchFlags(1)}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium"
                    style={{
                      backgroundColor: "var(--color-soft)",
                      color: "var(--color-text)",
                    }}
                  >
                    1
                  </button>
                  {startPage > 2 && (
                    <span className="px-1 text-xs theme-text-muted">…</span>
                  )}
                </>
              )}
              {pageNumbers.map((p) => (
                <button
                  key={p}
                  onClick={() => fetchFlags(p)}
                  className="rounded-lg px-3 py-1.5 text-xs font-medium transition"
                  style={{
                    backgroundColor:
                      p === page ? "var(--color-accent)" : "var(--color-soft)",
                    color: p === page ? "#fff" : "var(--color-text)",
                  }}
                >
                  {p}
                </button>
              ))}
              {endPage < totalPages && (
                <>
                  {endPage < totalPages - 1 && (
                    <span className="px-1 text-xs theme-text-muted">…</span>
                  )}
                  <button
                    onClick={() => fetchFlags(totalPages)}
                    className="rounded-lg px-3 py-1.5 text-xs font-medium"
                    style={{
                      backgroundColor: "var(--color-soft)",
                      color: "var(--color-text)",
                    }}
                  >
                    {totalPages}
                  </button>
                </>
              )}
              <button
                onClick={() => fetchFlags(page + 1)}
                disabled={!hasNext}
                className="rounded-lg px-3 py-1.5 text-xs font-medium transition"
                style={{
                  backgroundColor: !hasNext
                    ? "transparent"
                    : "var(--color-soft)",
                  color: !hasNext
                    ? "var(--color-text-muted)"
                    : "var(--color-text)",
                  opacity: !hasNext ? 0.5 : 1,
                }}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}

      {/* Review modal (unchanged) */}
      {reviewModalOpen && selectedFlag && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="relative w-full max-w-md rounded-2xl p-6 shadow-xl theme-card">
            <h3 className="text-lg font-semibold theme-text">Review Flag</h3>

            <div className="mt-3 space-y-2 text-sm">
              <p className="theme-text-muted">
                <span className="font-medium">Reason:</span>{" "}
                {selectedFlag.reason}
              </p>
              <p className="theme-text-muted">
                <span className="font-medium">Reported by:</span>{" "}
                {selectedFlag.reported_by}
              </p>
              <div
                className="theme-text-muted italic border-l-2 pl-3 py-1"
                style={{ borderColor: "var(--color-border)" }}
              >
                "{selectedFlag.message_snippet}"
              </div>
            </div>

            <div className="mt-4">
              <label className="block text-xs font-medium theme-text-muted mb-1">
                Resolution Note
              </label>
              <textarea
                value={resolutionNote}
                onChange={(e) => setResolutionNote(e.target.value)}
                rows={3}
                placeholder="Add a note or reason for resolution..."
                className="w-full rounded-lg border px-3 py-2 text-sm resize-none"
                style={{
                  borderColor: "var(--color-border)",
                  backgroundColor: "var(--color-soft)",
                  color: "var(--color-text)",
                }}
              />
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setReviewModalOpen(false)}
                className="rounded-lg px-4 py-2 text-sm font-medium transition-colors"
                style={{
                  backgroundColor: "var(--color-soft)",
                  color: "var(--color-text-muted)",
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleSubmitReview}
                disabled={submittingReview}
                className="rounded-lg px-4 py-2 text-sm font-medium text-white transition-opacity"
                style={{ backgroundColor: "var(--color-accent)" }}
              >
                {submittingReview ? "Saving…" : "Save Review"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
