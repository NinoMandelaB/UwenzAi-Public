// src/components/MemoryPanel.jsx
//
// Brain icon + popup next to the calendar icon in the chat header. Shows the
// logged-in user everything the current character remembers about them —
// memory facts, knowledge-graph connections, and the rolling conversation
// summary — with edit / delete / add. This is a correction layer over the
// SAME rows the automatic memory pipeline writes; it never changes how
// memories are generated and never shows or touches the relationship status.
//
// Every API call is scoped server-side to (requesting user, this character),
// so memories can never leak between characters or users.

import { useCallback, useEffect, useState } from "react";
import { Brain, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";

import { useLanguage } from "../context/LanguageContext";
import { useTheme } from "../context/ThemeContext";
import { useAuth } from "../context/AuthContext";
import { usePanelLifetime } from "../hooks/usePanelLifetime";
import { useModalDialog } from "../hooks/useModalDialog";
import {
  validateMemory,
  validateFact,
  validateRelation,
  validateSummary,
} from "../services/panelPayloads";
import { characterCount, SUMMARY_EDIT_LIMIT } from "../utils/textLimits";
import {
  createMemoryFact,
  createMemoryRelation,
  deleteMemoryFact,
  deleteMemoryRelation,
  deleteMemorySummary,
  listMemory,
  updateMemoryFact,
  updateMemoryRelation,
  updateMemorySummary,
} from "../services/memoryService";

function prettyPredicate(predicate) {
  return (predicate || "").replace(/_/g, " ");
}

function emptyFactDraft() {
  return "";
}

function emptyRelationDraft() {
  return { subject: "", predicate: "", object: "" };
}

export default function MemoryButton({ characterId, charName }) {
  const [open, setOpen] = useState(false);
  const { t } = useLanguage();

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-testid="memory-panel-toggle"
        className="rounded-full p-2 hover:bg-gray-200 dark:hover:bg-gray-800 text-[var(--color-text-muted)]"
        title={t("memory.toggle")}
        aria-label={t("memory.toggle")}
      >
        <Brain size={16} />
      </button>
      {open && (
        <MemoryPanel
          characterId={characterId}
          charName={charName}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

export function MemoryPanel({ characterId, charName, onClose }) {
  const { user, sessionGeneration } = useAuth();
  return (
    <ScopedMemoryPanel
      key={`${user?.user_id}:${sessionGeneration}:${characterId}`}
      characterId={characterId}
      charName={charName}
      onClose={onClose}
    />
  );
}

const versionOf = (record) => ({
  ...(typeof record?.updated_at === "string"
    ? { expected_updated_at: record.updated_at }
    : {}),
  ...(record?.summary_id ? { expected_summary_id: record.summary_id } : {}),
});

function ScopedMemoryPanel({ characterId, charName, onClose }) {
  const { isDark } = useTheme();
  const { t } = useLanguage();
  const current = usePanelLifetime();
  const dialogRef = useModalDialog(onClose);

  const [facts, setFacts] = useState([]);
  const [relations, setRelations] = useState([]);
  const [summary, setSummary] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [formError, setFormError] = useState(null);
  const [pagination, setPagination] = useState(null);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [editVersion, setEditVersion] = useState({});
  const [reviewingLatest, setReviewingLatest] = useState(false);
  const [missingLatest, setMissingLatest] = useState([]);

  // Fact add / edit
  const [showAddFact, setShowAddFact] = useState(false);
  const [factDraft, setFactDraft] = useState(emptyFactDraft());
  const [editingFactId, setEditingFactId] = useState(null);
  const [factEditDraft, setFactEditDraft] = useState(emptyFactDraft());
  const [pendingDeleteFactId, setPendingDeleteFactId] = useState(null);

  // Relation add / edit
  const [showAddRelation, setShowAddRelation] = useState(false);
  const [relationDraft, setRelationDraft] = useState(emptyRelationDraft());
  const [editingRelationId, setEditingRelationId] = useState(null);
  const [relationEditDraft, setRelationEditDraft] =
    useState(emptyRelationDraft());
  const [pendingDeleteRelationId, setPendingDeleteRelationId] = useState(null);

  // Summary edit / delete
  const [isEditingSummary, setIsEditingSummary] = useState(false);
  const [summaryDraft, setSummaryDraft] = useState("");
  const [pendingSummaryDelete, setPendingSummaryDelete] = useState(false);

  const refresh = useCallback(async () => {
    if (!characterId) return;
    setIsEditingSummary(false);
    setEditingFactId(null);
    setEditingRelationId(null);
    setSummaryDraft("");
    setFactEditDraft("");
    setRelationEditDraft(emptyRelationDraft());
    setPendingDeleteFactId(null);
    setPendingDeleteRelationId(null);
    setPendingSummaryDelete(false);
    setShowAddFact(false);
    setShowAddRelation(false);
    setFormError(null);
    setIsLoading(true);
    setLoadError(null);
    try {
      const data = validateMemory(await current(listMemory(characterId)));
      setFacts(data.facts);
      setRelations(data.relations);
      setSummary(data.summary);
      setPagination(data.pagination);
    } catch (err) {
      setLoadError(err.message || t("memory.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [characterId, t, current]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const loadMore = async () => {
    if (isLoadingMore || isSaving || !pagination) return;
    setIsLoadingMore(true);
    setFormError(null);
    try {
      const data = validateMemory(
        await current(
          listMemory(characterId, {
            page: pagination.page + 1,
            pageSize: pagination.page_size,
          }),
        ),
      );
      const append = (before, after, key) => {
        const known = new Set(before.map((row) => row[key]));
        return [...before, ...after.filter((row) => !known.has(row[key]))];
      };
      setFacts((prev) => append(prev, data.facts, "memory_fact_id"));
      setRelations((prev) =>
        append(prev, data.relations, "memory_relation_id"),
      );
      setPagination(data.pagination);
    } catch (err) {
      setFormError(err.message || t("memory.loadError"));
    } finally {
      setIsLoadingMore(false);
    }
  };
  const mutationError = (err, fallback) =>
    setFormError(
      err.status === 409 ? t("memory.staleError") : err.message || t(fallback),
    );

  const reloadKeepingDraft = async () => {
    if (isLoading || isSaving || isLoadingMore) return;
    setIsLoading(true);
    try {
      const latest = validateMemory(await current(listMemory(characterId)));
      for (let page = 2; page <= (pagination?.page || 1); page += 1) {
        const next = validateMemory(
          await current(
            listMemory(characterId, {
              page,
              pageSize: pagination.page_size,
            }),
          ),
        );
        latest.facts.push(...next.facts);
        latest.relations.push(...next.relations);
        latest.pagination = next.pagination;
      }
      const unique = (rows, key) => [
        ...new Map(rows.map((row) => [row[key], row])).values(),
      ];
      const freshFacts = unique(latest.facts, "memory_fact_id");
      const freshRelations = unique(latest.relations, "memory_relation_id");
      const missing = [];
      const retainDraftRow = (fresh, before, key, editingId) => {
        if (editingId && !fresh.some((row) => row[key] === editingId)) {
          missing.push(editingId);
          return [...fresh, ...before.filter((row) => row[key] === editingId)];
        }
        return fresh;
      };
      setFacts(
        retainDraftRow(freshFacts, facts, "memory_fact_id", editingFactId),
      );
      setRelations(
        retainDraftRow(
          freshRelations,
          relations,
          "memory_relation_id",
          editingRelationId,
        ),
      );
      if (!latest.summary && isEditingSummary) missing.push("summary");
      else setSummary(latest.summary);
      setPagination(latest.pagination);
      setEditVersion((prev) => ({
        ...prev,
        ...Object.fromEntries(
          freshFacts.map((row) => [row.memory_fact_id, versionOf(row)]),
        ),
        ...Object.fromEntries(
          freshRelations.map((row) => [row.memory_relation_id, versionOf(row)]),
        ),
        ...(latest.summary ? { summary: versionOf(latest.summary) } : {}),
      }));
      // A deliberate reload rebases available rows, but never discards typed
      // text or turns an absent/deleted target into an unconditional update.
      setMissingLatest(missing);
      setReviewingLatest(true);
      setFormError(t("memory.reviewLatest"));
    } catch (err) {
      setFormError(err.message || t("memory.loadError"));
    } finally {
      setIsLoading(false);
    }
  };

  // ---- Facts ------------------------------------------------------------

  const handleAddFact = async (e) => {
    e.preventDefault();
    const text = factDraft.trim();
    if (!text) {
      setFormError(t("memory.factRequired"));
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      const created = validateFact(
        await current(createMemoryFact(characterId, text)),
      );
      setFacts((prev) => [
        created,
        ...prev.filter((row) => row.memory_fact_id !== created.memory_fact_id),
      ]);
      setFactDraft(emptyFactDraft());
      setShowAddFact(false);
    } catch (err) {
      setFormError(err.message || t("memory.saveError"));
    } finally {
      setIsSaving(false);
    }
  };

  const startEditFact = (fact) => {
    setEditingFactId(fact.memory_fact_id);
    setFactEditDraft(fact.fact_text);
    setEditVersion((prev) => ({
      ...prev,
      [fact.memory_fact_id]: versionOf(fact),
    }));
    setFormError(null);
  };

  const handleFactEditSave = async (factId) => {
    const text = factEditDraft.trim();
    if (!text) {
      setFormError(t("memory.factRequired"));
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      const updated = validateFact(
        await current(updateMemoryFact(factId, text, editVersion[factId])),
      );
      setFacts((prev) =>
        prev.map((f) => (f.memory_fact_id === factId ? updated : f)),
      );
      setEditingFactId(null);
    } catch (err) {
      mutationError(err, "memory.saveError");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteFact = async (factId) => {
    setIsSaving(true);
    try {
      await current(
        deleteMemoryFact(
          factId,
          versionOf(facts.find((row) => row.memory_fact_id === factId)),
        ),
      );
      setFacts((prev) => prev.filter((f) => f.memory_fact_id !== factId));
      setPendingDeleteFactId(null);
    } catch (err) {
      mutationError(err, "memory.deleteError");
    } finally {
      setIsSaving(false);
    }
  };

  // ---- Relations ----------------------------------------------------------

  const handleAddRelation = async (e) => {
    e.preventDefault();
    const { subject, predicate, object } = relationDraft;
    if (!subject.trim() || !predicate.trim()) {
      setFormError(t("memory.relationRequired"));
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      const created = validateRelation(
        await current(
          createMemoryRelation(characterId, {
            subject: subject.trim(),
            predicate: predicate.trim(),
            object: object.trim(),
          }),
        ),
      );
      setRelations((prev) => [
        created,
        ...prev.filter(
          (row) => row.memory_relation_id !== created.memory_relation_id,
        ),
      ]);
      setRelationDraft(emptyRelationDraft());
      setShowAddRelation(false);
    } catch (err) {
      setFormError(err.message || t("memory.saveError"));
    } finally {
      setIsSaving(false);
    }
  };

  const startEditRelation = (relation) => {
    setEditVersion((prev) => ({
      ...prev,
      [relation.memory_relation_id]: versionOf(relation),
    }));
    setEditingRelationId(relation.memory_relation_id);
    setRelationEditDraft({
      subject: relation.subject,
      predicate: prettyPredicate(relation.predicate),
      object: relation.object,
    });
    setFormError(null);
  };

  const handleRelationEditSave = async (relationId) => {
    const { subject, predicate, object } = relationEditDraft;
    if (!subject.trim() || !predicate.trim()) {
      setFormError(t("memory.relationRequired"));
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      const updated = validateRelation(
        await current(
          updateMemoryRelation(
            relationId,
            {
              subject: subject.trim(),
              predicate: predicate.trim(),
              object: object.trim(),
            },
            editVersion[relationId],
          ),
        ),
      );
      setRelations((prev) =>
        prev.map((r) => (r.memory_relation_id === relationId ? updated : r)),
      );
      setEditingRelationId(null);
    } catch (err) {
      mutationError(err, "memory.saveError");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteRelation = async (relationId) => {
    setIsSaving(true);
    try {
      await current(
        deleteMemoryRelation(
          relationId,
          versionOf(
            relations.find((row) => row.memory_relation_id === relationId),
          ),
        ),
      );
      setRelations((prev) =>
        prev.filter((r) => r.memory_relation_id !== relationId),
      );
      setPendingDeleteRelationId(null);
    } catch (err) {
      mutationError(err, "memory.deleteError");
    } finally {
      setIsSaving(false);
    }
  };

  // ---- Summary --------------------------------------------------------------

  const startEditSummary = () => {
    setEditVersion((prev) => ({ ...prev, summary: versionOf(summary) }));
    setSummaryDraft(summary?.summary_text || "");
    setIsEditingSummary(true);
    setFormError(null);
  };

  const handleSummarySave = async () => {
    const text = summaryDraft.trim();
    if (!text) {
      setFormError(t("memory.summaryRequired"));
      return;
    }
    if (characterCount(text) > SUMMARY_EDIT_LIMIT) {
      setFormError(t("memory.summaryTooLong"));
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      const updated = validateSummary(
        await current(
          updateMemorySummary(characterId, text, editVersion.summary),
        ),
        false,
      );
      setSummary(updated);
      setIsEditingSummary(false);
    } catch (err) {
      mutationError(err, "memory.saveError");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteSummary = async () => {
    setIsSaving(true);
    try {
      await current(deleteMemorySummary(characterId, versionOf(summary)));
      setSummary(null);
      setPendingSummaryDelete(false);
    } catch (err) {
      mutationError(err, "memory.deleteError");
    } finally {
      setIsSaving(false);
    }
  };

  // ---- Render -----------------------------------------------------------------

  const textColor = isDark ? "#f8fafc" : "#0f172a";
  const mutedColor = isDark ? "#94a3b8" : "#64748b";
  const borderColor = isDark ? "rgba(255,255,255,0.08)" : "rgba(15,23,42,0.08)";
  const inputBg = isDark ? "rgba(255,255,255,0.05)" : "rgba(15,23,42,0.03)";

  const inputStyle = {
    background: inputBg,
    border: `1px solid ${borderColor}`,
    color: textColor,
  };

  const sectionLabel = "text-xs font-semibold uppercase tracking-wide";
  const smallBtn =
    "rounded-lg px-2.5 py-1.5 text-xs font-medium transition disabled:opacity-50 disabled:cursor-not-allowed";

  return (
    <div
      ref={dialogRef}
      className="fixed inset-0 z-[999] flex items-center justify-center p-3 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label={t("memory.title")}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 backdrop-blur-sm"
        style={{
          backgroundColor: isDark ? "rgba(0,0,0,0.5)" : "rgba(15,23,42,0.16)",
        }}
        onClick={onClose}
      />

      {/* Panel */}
      <div
        className="relative flex w-full max-w-md flex-col rounded-2xl border shadow-2xl animate-in fade-in zoom-in-95 duration-200"
        style={{
          background: isDark
            ? "rgba(15, 23, 42, 0.98)"
            : "rgba(255,255,255,0.98)",
          borderColor,
          maxHeight: "min(85vh, 640px)",
          backdropFilter: "blur(16px)",
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between gap-2 border-b px-4 py-3"
          style={{ borderColor }}
        >
          <div className="flex items-center gap-2">
            <Brain size={18} style={{ color: textColor }} />
            <h2
              className="text-base font-semibold"
              style={{ color: textColor }}
            >
              {t("memory.title")}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("memory.close")}
            className="flex h-8 w-8 items-center justify-center rounded-full transition-all duration-200 hover:scale-105"
            style={{ background: inputBg, color: mutedColor }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Intro */}
        <p
          className="px-4 pt-3 text-xs leading-relaxed"
          style={{ color: mutedColor }}
        >
          {t("memory.description", { name: charName })}
        </p>

        {/* Scrollable content */}
        <fieldset
          disabled={isSaving || isLoadingMore}
          className="min-w-0 flex-1 overflow-y-auto px-4 py-3 space-y-5"
        >
          {formError && (
            <p className="text-xs text-red-400" role="alert">
              {formError}
              <button
                type="button"
                onClick={reloadKeepingDraft}
                className="block underline"
              >
                {t("memory.reloadLatest")}
              </button>
            </p>
          )}

          {isLoading ? (
            <div
              className="flex items-center justify-center gap-2 py-8 text-sm"
              style={{ color: mutedColor }}
            >
              <Loader2 size={16} className="animate-spin" />
              {t("memory.loading")}
            </div>
          ) : loadError ? (
            <div className="py-8 text-center">
              <p role="alert" className="mb-3 text-sm text-red-400">
                {loadError}
              </p>
              <button
                type="button"
                onClick={refresh}
                className={`${smallBtn} border`}
                style={inputStyle}
              >
                {t("memory.retry")}
              </button>
            </div>
          ) : (
            <>
              {/* ---- Facts ---- */}
              <section aria-label={t("memory.facts.title")}>
                <div className="mb-2 flex items-center justify-between">
                  <h3 className={sectionLabel} style={{ color: mutedColor }}>
                    {t("memory.facts.title")}
                  </h3>
                  <button
                    type="button"
                    onClick={() => {
                      setShowAddFact((v) => !v);
                      setFormError(null);
                    }}
                    className={`${smallBtn} border`}
                    style={inputStyle}
                  >
                    <span className="inline-flex items-center gap-1">
                      <Plus size={12} />
                      {t("memory.facts.add")}
                    </span>
                  </button>
                </div>

                {showAddFact && (
                  <form onSubmit={handleAddFact} className="mb-2 space-y-2">
                    <textarea
                      value={factDraft}
                      onChange={(e) => setFactDraft(e.target.value)}
                      placeholder={t("memory.facts.placeholder")}
                      rows={2}
                      maxLength={500}
                      className="w-full rounded-xl px-3 py-2 text-sm outline-none"
                      style={inputStyle}
                    />
                    <div className="flex gap-2">
                      <button
                        type="submit"
                        disabled={isSaving}
                        className={`${smallBtn}`}
                        style={{
                          background: "rgba(99,102,241,0.15)",
                          color: "#818cf8",
                        }}
                      >
                        {isSaving ? t("memory.saving") : t("memory.add")}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setShowAddFact(false);
                          setFactDraft(emptyFactDraft());
                        }}
                        className={`${smallBtn} border`}
                        style={inputStyle}
                      >
                        {t("memory.cancel")}
                      </button>
                    </div>
                  </form>
                )}

                {facts.length === 0 ? (
                  <p className="text-xs" style={{ color: mutedColor }}>
                    {t("memory.facts.empty", { name: charName })}
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {facts.map((fact) => {
                      const isEditing = editingFactId === fact.memory_fact_id;
                      const isPendingDelete =
                        pendingDeleteFactId === fact.memory_fact_id;
                      return (
                        <li
                          key={fact.memory_fact_id}
                          className="rounded-xl border p-3"
                          style={{ borderColor }}
                        >
                          {isEditing ? (
                            <div className="space-y-2">
                              {reviewingLatest && (
                                <p className="text-xs theme-text-muted">
                                  {missingLatest.includes(fact.memory_fact_id)
                                    ? t("memory.latestUnavailable")
                                    : `${t("memory.latestVersion")}: ${fact.fact_text}`}
                                </p>
                              )}
                              <textarea
                                value={factEditDraft}
                                onChange={(e) =>
                                  setFactEditDraft(e.target.value)
                                }
                                rows={2}
                                maxLength={500}
                                className="w-full rounded-xl px-3 py-2 text-sm outline-none"
                                style={inputStyle}
                              />
                              <div className="flex gap-2">
                                <button
                                  type="button"
                                  onClick={() => setEditingFactId(null)}
                                  className={`${smallBtn} border`}
                                  style={inputStyle}
                                >
                                  {t("memory.cancel")}
                                </button>
                                <button
                                  type="button"
                                  disabled={isSaving}
                                  onClick={() =>
                                    handleFactEditSave(fact.memory_fact_id)
                                  }
                                  className={smallBtn}
                                  style={{
                                    background: "rgba(99,102,241,0.15)",
                                    color: "#818cf8",
                                  }}
                                >
                                  {isSaving
                                    ? t("memory.saving")
                                    : t("memory.save")}
                                </button>
                              </div>
                            </div>
                          ) : (
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <p
                                  className="text-sm break-words"
                                  style={{ color: textColor }}
                                >
                                  {fact.fact_text}
                                </p>
                                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                                  {fact.shared && (
                                    <span
                                      className="rounded-full px-2 py-0.5 text-[10px] font-medium"
                                      style={{
                                        background: "rgba(99,102,241,0.12)",
                                        color: "#818cf8",
                                      }}
                                    >
                                      {t("memory.shared")}
                                    </span>
                                  )}
                                </div>
                              </div>
                              <div className="flex shrink-0 items-center gap-1">
                                {isPendingDelete ? (
                                  <>
                                    <button
                                      type="button"
                                      onClick={() =>
                                        setPendingDeleteFactId(null)
                                      }
                                      className={`${smallBtn} border`}
                                      style={inputStyle}
                                    >
                                      {t("memory.cancel")}
                                    </button>
                                    <button
                                      type="button"
                                      disabled={isSaving}
                                      onClick={() =>
                                        handleDeleteFact(fact.memory_fact_id)
                                      }
                                      className={smallBtn}
                                      style={{
                                        background: "rgba(239,68,68,0.12)",
                                        color: "#f87171",
                                      }}
                                    >
                                      {t("memory.confirmDelete")}
                                    </button>
                                  </>
                                ) : (
                                  <>
                                    <button
                                      type="button"
                                      onClick={() => startEditFact(fact)}
                                      aria-label={t("memory.edit")}
                                      title={t("memory.edit")}
                                      className="rounded-lg p-1.5 hover:bg-black/5 dark:hover:bg-white/5"
                                      style={{ color: mutedColor }}
                                    >
                                      <Pencil size={14} />
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() =>
                                        setPendingDeleteFactId(
                                          fact.memory_fact_id,
                                        )
                                      }
                                      aria-label={t("memory.delete")}
                                      title={t("memory.delete")}
                                      className="rounded-lg p-1.5 hover:bg-black/5 dark:hover:bg-white/5"
                                      style={{ color: mutedColor }}
                                    >
                                      <Trash2 size={14} />
                                    </button>
                                  </>
                                )}
                              </div>
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>

              {/* ---- Relations ---- */}
              <section aria-label={t("memory.relations.title")}>
                <div className="mb-2 flex items-center justify-between">
                  <h3 className={sectionLabel} style={{ color: mutedColor }}>
                    {t("memory.relations.title")}
                  </h3>
                  <button
                    type="button"
                    onClick={() => {
                      setShowAddRelation((v) => !v);
                      setFormError(null);
                    }}
                    className={`${smallBtn} border`}
                    style={inputStyle}
                  >
                    <span className="inline-flex items-center gap-1">
                      <Plus size={12} />
                      {t("memory.relations.add")}
                    </span>
                  </button>
                </div>

                {showAddRelation && (
                  <form onSubmit={handleAddRelation} className="mb-2 space-y-2">
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                      <input
                        value={relationDraft.subject}
                        onChange={(e) =>
                          setRelationDraft((d) => ({
                            ...d,
                            subject: e.target.value,
                          }))
                        }
                        placeholder={t("memory.relations.subjectPlaceholder")}
                        maxLength={120}
                        className="rounded-xl px-3 py-2 text-sm outline-none"
                        style={inputStyle}
                      />
                      <input
                        value={relationDraft.predicate}
                        onChange={(e) =>
                          setRelationDraft((d) => ({
                            ...d,
                            predicate: e.target.value,
                          }))
                        }
                        placeholder={t("memory.relations.predicatePlaceholder")}
                        maxLength={60}
                        className="rounded-xl px-3 py-2 text-sm outline-none"
                        style={inputStyle}
                      />
                      <input
                        value={relationDraft.object}
                        onChange={(e) =>
                          setRelationDraft((d) => ({
                            ...d,
                            object: e.target.value,
                          }))
                        }
                        placeholder={t("memory.relations.objectPlaceholder")}
                        maxLength={120}
                        className="rounded-xl px-3 py-2 text-sm outline-none"
                        style={inputStyle}
                      />
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="submit"
                        disabled={isSaving}
                        className={smallBtn}
                        style={{
                          background: "rgba(99,102,241,0.15)",
                          color: "#818cf8",
                        }}
                      >
                        {isSaving ? t("memory.saving") : t("memory.add")}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setShowAddRelation(false);
                          setRelationDraft(emptyRelationDraft());
                        }}
                        className={`${smallBtn} border`}
                        style={inputStyle}
                      >
                        {t("memory.cancel")}
                      </button>
                    </div>
                  </form>
                )}

                {relations.length === 0 ? (
                  <p className="text-xs" style={{ color: mutedColor }}>
                    {t("memory.relations.empty")}
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {relations.map((relation) => {
                      const isEditing =
                        editingRelationId === relation.memory_relation_id;
                      const isPendingDelete =
                        pendingDeleteRelationId === relation.memory_relation_id;
                      return (
                        <li
                          key={relation.memory_relation_id}
                          className="rounded-xl border p-3"
                          style={{ borderColor }}
                        >
                          {isEditing ? (
                            <div className="space-y-2">
                              {reviewingLatest && (
                                <p className="text-xs theme-text-muted">
                                  {missingLatest.includes(
                                    relation.memory_relation_id,
                                  )
                                    ? t("memory.latestUnavailable")
                                    : `${t("memory.latestVersion")}: ${relation.subject} — ${relation.predicate} — ${relation.object}`}
                                </p>
                              )}
                              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                                <input
                                  value={relationEditDraft.subject}
                                  onChange={(e) =>
                                    setRelationEditDraft((d) => ({
                                      ...d,
                                      subject: e.target.value,
                                    }))
                                  }
                                  placeholder={t(
                                    "memory.relations.subjectPlaceholder",
                                  )}
                                  maxLength={120}
                                  className="rounded-xl px-3 py-2 text-sm outline-none"
                                  style={inputStyle}
                                />
                                <input
                                  value={relationEditDraft.predicate}
                                  onChange={(e) =>
                                    setRelationEditDraft((d) => ({
                                      ...d,
                                      predicate: e.target.value,
                                    }))
                                  }
                                  placeholder={t(
                                    "memory.relations.predicatePlaceholder",
                                  )}
                                  maxLength={60}
                                  className="rounded-xl px-3 py-2 text-sm outline-none"
                                  style={inputStyle}
                                />
                                <input
                                  value={relationEditDraft.object}
                                  onChange={(e) =>
                                    setRelationEditDraft((d) => ({
                                      ...d,
                                      object: e.target.value,
                                    }))
                                  }
                                  placeholder={t(
                                    "memory.relations.objectPlaceholder",
                                  )}
                                  maxLength={120}
                                  className="rounded-xl px-3 py-2 text-sm outline-none"
                                  style={inputStyle}
                                />
                              </div>
                              <div className="flex gap-2">
                                <button
                                  type="button"
                                  onClick={() => setEditingRelationId(null)}
                                  className={`${smallBtn} border`}
                                  style={inputStyle}
                                >
                                  {t("memory.cancel")}
                                </button>
                                <button
                                  type="button"
                                  disabled={isSaving}
                                  onClick={() =>
                                    handleRelationEditSave(
                                      relation.memory_relation_id,
                                    )
                                  }
                                  className={smallBtn}
                                  style={{
                                    background: "rgba(99,102,241,0.15)",
                                    color: "#818cf8",
                                  }}
                                >
                                  {isSaving
                                    ? t("memory.saving")
                                    : t("memory.save")}
                                </button>
                              </div>
                            </div>
                          ) : (
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <p
                                  className="text-sm break-words"
                                  style={{ color: textColor }}
                                >
                                  {relation.subject}{" "}
                                  <span style={{ color: mutedColor }}>
                                    {prettyPredicate(relation.predicate)}
                                  </span>{" "}
                                  {relation.object}
                                </p>
                                {relation.shared && (
                                  <span
                                    className="mt-1 inline-block rounded-full px-2 py-0.5 text-[10px] font-medium"
                                    style={{
                                      background: "rgba(99,102,241,0.12)",
                                      color: "#818cf8",
                                    }}
                                  >
                                    {t("memory.shared")}
                                  </span>
                                )}
                              </div>
                              <div className="flex shrink-0 items-center gap-1">
                                {isPendingDelete ? (
                                  <>
                                    <button
                                      type="button"
                                      onClick={() =>
                                        setPendingDeleteRelationId(null)
                                      }
                                      className={`${smallBtn} border`}
                                      style={inputStyle}
                                    >
                                      {t("memory.cancel")}
                                    </button>
                                    <button
                                      type="button"
                                      disabled={isSaving}
                                      onClick={() =>
                                        handleDeleteRelation(
                                          relation.memory_relation_id,
                                        )
                                      }
                                      className={smallBtn}
                                      style={{
                                        background: "rgba(239,68,68,0.12)",
                                        color: "#f87171",
                                      }}
                                    >
                                      {t("memory.confirmDelete")}
                                    </button>
                                  </>
                                ) : (
                                  <>
                                    <button
                                      type="button"
                                      onClick={() =>
                                        startEditRelation(relation)
                                      }
                                      aria-label={t("memory.edit")}
                                      title={t("memory.edit")}
                                      className="rounded-lg p-1.5 hover:bg-black/5 dark:hover:bg-white/5"
                                      style={{ color: mutedColor }}
                                    >
                                      <Pencil size={14} />
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() =>
                                        setPendingDeleteRelationId(
                                          relation.memory_relation_id,
                                        )
                                      }
                                      aria-label={t("memory.delete")}
                                      title={t("memory.delete")}
                                      className="rounded-lg p-1.5 hover:bg-black/5 dark:hover:bg-white/5"
                                      style={{ color: mutedColor }}
                                    >
                                      <Trash2 size={14} />
                                    </button>
                                  </>
                                )}
                              </div>
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>

              {/* ---- Summary ---- */}
              <section aria-label={t("memory.summary.title")}>
                <h3
                  className={`${sectionLabel} mb-2`}
                  style={{ color: mutedColor }}
                >
                  {t("memory.summary.title")}
                </h3>

                {summary === null ? (
                  <p className="text-xs" style={{ color: mutedColor }}>
                    {t("memory.summary.empty")}
                  </p>
                ) : isEditingSummary ? (
                  <div className="space-y-2">
                    {reviewingLatest && (
                      <p className="text-xs theme-text-muted">
                        {missingLatest.includes("summary")
                          ? t("memory.latestUnavailable")
                          : `${t("memory.latestVersion")}: ${summary.summary_text}`}
                      </p>
                    )}
                    <textarea
                      value={summaryDraft}
                      onChange={(e) => setSummaryDraft(e.target.value)}
                      rows={4}
                      className="w-full rounded-xl px-3 py-2 text-sm outline-none"
                      style={inputStyle}
                    />
                    <p className="text-xs" role="status">
                      {characterCount(summaryDraft)} / {SUMMARY_EDIT_LIMIT}
                    </p>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => setIsEditingSummary(false)}
                        className={`${smallBtn} border`}
                        style={inputStyle}
                      >
                        {t("memory.cancel")}
                      </button>
                      <button
                        type="button"
                        disabled={
                          isSaving ||
                          characterCount(summaryDraft.trim()) >
                            SUMMARY_EDIT_LIMIT
                        }
                        onClick={handleSummarySave}
                        className={smallBtn}
                        style={{
                          background: "rgba(99,102,241,0.15)",
                          color: "#818cf8",
                        }}
                      >
                        {isSaving ? t("memory.saving") : t("memory.save")}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div
                    className="rounded-xl border p-3"
                    style={{ borderColor }}
                  >
                    <p
                      className="text-sm leading-relaxed break-words"
                      style={{ color: textColor }}
                    >
                      {summary.summary_text}
                    </p>
                    <div className="mt-2 flex items-center justify-end gap-1">
                      {pendingSummaryDelete ? (
                        <>
                          <button
                            type="button"
                            onClick={() => setPendingSummaryDelete(false)}
                            className={`${smallBtn} border`}
                            style={inputStyle}
                          >
                            {t("memory.cancel")}
                          </button>
                          <button
                            type="button"
                            disabled={isSaving}
                            onClick={handleDeleteSummary}
                            className={smallBtn}
                            style={{
                              background: "rgba(239,68,68,0.12)",
                              color: "#f87171",
                            }}
                          >
                            {t("memory.confirmDelete")}
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            type="button"
                            onClick={startEditSummary}
                            aria-label={t("memory.edit")}
                            title={t("memory.edit")}
                            className="rounded-lg p-1.5 hover:bg-black/5 dark:hover:bg-white/5"
                            style={{ color: mutedColor }}
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => setPendingSummaryDelete(true)}
                            aria-label={t("memory.delete")}
                            title={t("memory.delete")}
                            className="rounded-lg p-1.5 hover:bg-black/5 dark:hover:bg-white/5"
                            style={{ color: mutedColor }}
                          >
                            <Trash2 size={14} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </section>
            </>
          )}
          {(pagination?.facts.has_next || pagination?.relations.has_next) && (
            <button type="button" onClick={loadMore} className={smallBtn}>
              {isLoadingMore ? t("memory.loading") : t("memory.loadMore")}
            </button>
          )}
        </fieldset>

        {/* Footer / privacy note */}
        <div className="border-t px-4 py-3" style={{ borderColor }}>
          <p
            className="text-[11px] leading-relaxed"
            style={{ color: mutedColor }}
          >
            {t("memory.privacyNote", { name: charName })}
          </p>
        </div>
      </div>
    </div>
  );
}
