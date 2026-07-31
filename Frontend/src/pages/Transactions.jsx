// src/pages/Transactions.jsx
import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import PageCard from "../components/PageCard";
import SectionTitle from "../components/SectionTitle";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import { getSparkTransactionsPaginated } from "../services/billingService";

function formatDateTime(isoString) {
  if (!isoString) return "";
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now - date;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hr ago`;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const PAGE_SIZE = 20;

function getDateISO(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export default function Transactions() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { isAuthenticated } = useAuth();

  const today = getDateISO(new Date());
  const thirtyDaysAgo = getDateISO(
    new Date(Date.now() - 30 * 24 * 60 * 60 * 1000),
  );

  const [transactions, setTransactions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingPage, setLoadingPage] = useState(false);
  const [error, setError] = useState(null);
  const [total, setTotal] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const totalPages = Math.ceil(total / PAGE_SIZE);

  // Filters – default to last 30 days
  const [search, setSearch] = useState("");
  const [startDate, setStartDate] = useState(thirtyDaysAgo);
  const [endDate, setEndDate] = useState(today);
  const debounceRef = useRef(null);

  const fetchPage = useCallback(async (pageNum, currentFilters) => {
    try {
      const data = await getSparkTransactionsPaginated({
        page: pageNum,
        pageSize: PAGE_SIZE,
        ...currentFilters,
      });
      setTransactions(data.data.transactions);
      setTotal(data.data.total);
      setCurrentPage(pageNum);
    } catch (err) {
      setError(err.message || "Failed to load transactions");
    }
  }, []);

  // Fetch when filters or page change
  useEffect(() => {
    if (!isAuthenticated) {
      navigate("/login");
      return;
    }
    setLoading(true);
    const filters = { search, startDate, endDate };
    fetchPage(1, filters).finally(() => setLoading(false));
  }, [isAuthenticated, navigate, fetchPage, search, startDate, endDate]);

  const goToPage = async (pageNum) => {
    if (pageNum === currentPage) return;
    setLoadingPage(true);
    await fetchPage(pageNum, { search, startDate, endDate });
    setLoadingPage(false);
  };

  const handleSearchChange = (e) => {
    const value = e.target.value;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setSearch(value);
    }, 300);
  };

  const clearFilters = () => {
    setSearch("");
    setStartDate(thirtyDaysAgo);
    setEndDate(today);
  };

  if (!isAuthenticated) return null;

  // Generate page number buttons
  const pageNumbers = [];
  const maxVisible = 5;
  let startPage = Math.max(1, currentPage - Math.floor(maxVisible / 2));
  let endPage = startPage + maxVisible - 1;
  if (endPage > totalPages) {
    endPage = totalPages;
    startPage = Math.max(1, endPage - maxVisible + 1);
  }
  for (let i = startPage; i <= endPage; i++) {
    pageNumbers.push(i);
  }

  return (
    <div className="space-y-6 max-w-2xl mx-auto lg:mx-0">
      <SectionTitle>All Transactions</SectionTitle>

      {/* Filters – no card */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex-1">
          <label className="block text-xs font-medium theme-text-muted mb-1">
            Search
          </label>
          <input
            type="text"
            placeholder="Search descriptions..."
            onChange={handleSearchChange}
            className="w-full rounded-lg border px-3 py-2 text-sm"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-soft)",
              color: "var(--color-text)",
            }}
          />
        </div>
        <div className="flex gap-2">
          <div>
            <label className="block text-xs font-medium theme-text-muted mb-1">
              From
            </label>
            <input
              type="date"
              value={startDate}
              max={endDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="rounded-lg border px-3 py-2 text-sm"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text)",
              }}
            />
          </div>
          <div>
            <label className="block text-xs font-medium theme-text-muted mb-1">
              To
            </label>
            <input
              type="date"
              value={endDate}
              min={startDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="rounded-lg border px-3 py-2 text-sm"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text)",
              }}
            />
          </div>
          {(search || startDate !== thirtyDaysAgo || endDate !== today) && (
            <button
              onClick={clearFilters}
              className="self-end rounded-lg px-4 py-2 text-xs font-medium"
              style={{
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text-muted)",
              }}
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg bg-red-50 p-4 text-sm text-red-600">
          {error}
        </div>
      )}

      {/* Loading initial */}
      {loading ? (
        <div className="py-12 text-center text-sm theme-text-muted">
          Loading transactions…
        </div>
      ) : transactions.length === 0 ? (
        <div className="py-8 text-center text-sm theme-text-muted">
          No transactions found.
        </div>
      ) : (
        <>
          <div className="text-sm theme-text-muted">
            Showing {(currentPage - 1) * PAGE_SIZE + 1}–
            {Math.min(currentPage * PAGE_SIZE, total)} of {total} transactions
          </div>

          {/* Table inside card */}
          <PageCard>
            <div
              className="overflow-x-auto rounded-xl border"
              style={{ borderColor: "var(--color-border)" }}
            >
              <table className="min-w-full text-left text-sm">
                <thead
                  className="text-xs"
                  style={{
                    backgroundColor: "var(--color-soft)",
                    color: "var(--color-text-muted)",
                  }}
                >
                  <tr>
                    <th className="px-4 py-3 font-medium">Description</th>
                    <th className="px-4 py-3 font-medium text-right">Amount</th>
                    <th className="px-4 py-3 font-medium text-right hidden sm:table-cell">
                      Date
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.map((tx) => (
                    <tr
                      key={tx.id}
                      className="transition-colors hover:bg-[var(--color-soft)]"
                      style={{ borderBottom: "1px solid var(--color-border)" }}
                    >
                      <td className="px-4 py-3">
                        <p className="font-medium theme-text">
                          {tx.description}
                        </p>
                        <p className="text-xs theme-text-muted sm:hidden mt-0.5">
                          {formatDateTime(tx.created_at)}
                        </p>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums theme-text">
                        {tx.type === "credit" ? "+" : "-"}
                        {Math.abs(Number(tx.amount))} {tx.currency}
                      </td>
                      <td className="px-4 py-3 text-right text-xs theme-text-muted hidden sm:table-cell">
                        {formatDateTime(tx.created_at)}
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
                onClick={() => goToPage(currentPage - 1)}
                disabled={currentPage === 1 || loadingPage}
                className="rounded-lg px-3 py-1.5 text-xs font-medium transition"
                style={{
                  backgroundColor:
                    currentPage === 1 ? "transparent" : "var(--color-soft)",
                  color:
                    currentPage === 1
                      ? "var(--color-text-muted)"
                      : "var(--color-text)",
                  opacity: currentPage === 1 ? 0.5 : 1,
                }}
              >
                Prev
              </button>
              {startPage > 1 && (
                <>
                  <button
                    onClick={() => goToPage(1)}
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
                  onClick={() => goToPage(p)}
                  disabled={loadingPage}
                  className="rounded-lg px-3 py-1.5 text-xs font-medium transition"
                  style={{
                    backgroundColor:
                      p === currentPage
                        ? "var(--color-accent)"
                        : "var(--color-soft)",
                    color: p === currentPage ? "#fff" : "var(--color-text)",
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
                    onClick={() => goToPage(totalPages)}
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
                onClick={() => goToPage(currentPage + 1)}
                disabled={currentPage === totalPages || loadingPage}
                className="rounded-lg px-3 py-1.5 text-xs font-medium transition"
                style={{
                  backgroundColor:
                    currentPage === totalPages
                      ? "transparent"
                      : "var(--color-soft)",
                  color:
                    currentPage === totalPages
                      ? "var(--color-text-muted)"
                      : "var(--color-text)",
                  opacity: currentPage === totalPages ? 0.5 : 1,
                }}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
