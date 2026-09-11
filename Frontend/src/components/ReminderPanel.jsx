// src/components/ReminderPanel.jsx
//
// Calendar/task popup for the chat header: lists every pending reminder for
// the current user (across all characters/chats), and lets them add, edit,
// or delete reminders. New reminders are created against the conversation
// that is currently open in the chat modal. Works on mobile and desktop.

import { useCallback, useEffect, useState } from "react";
import { Calendar, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";

import { useTheme } from "../context/ThemeContext";
import { useAuth } from "../context/AuthContext";
import { resolveFeaturePreferences } from "../utils/featurePreferences";
import { usePanelLifetime } from "../hooks/usePanelLifetime";
import { useModalDialog } from "../hooks/useModalDialog";
import { validateReminder, validateReminders } from "../services/panelPayloads";
import {
  createReminder,
  deleteReminder,
  listReminders,
  updateReminder,
} from "../services/reminderService";

function toDateInputValue(dueAtLocal) {
  // due_at_local from the API is "YYYY-MM-DD HH:MM".
  return (dueAtLocal || "").split(" ")[0] || "";
}

function toTimeInputValue(dueAtLocal) {
  return (dueAtLocal || "").split(" ")[1] || "";
}

function emptyDraft() {
  return { reminderText: "", dueDate: "", dueTime: "" };
}

// All chat surfaces share this gate. Settings management deliberately does not.
export function ChatReminderButton(props) {
  const { user, isAuthenticated, isLoading, sessionGeneration } = useAuth();
  if (
    !isAuthenticated ||
    isLoading ||
    !resolveFeaturePreferences(user).reminders
  )
    return null;
  return (
    <ReminderButton
      key={`${user?.user_id}:${sessionGeneration}:${props.activeConvId}`}
      {...props}
      canCreate
    />
  );
}

export default function ReminderButton({
  activeConvId,
  charName,
  canCreate = false,
  label,
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-testid="reminder-panel-toggle"
        className="inline-flex items-center gap-2 rounded-full p-2 text-sm hover:bg-gray-200 dark:hover:bg-gray-800 text-[var(--color-text-muted)]"
        title="Reminders"
        aria-label={label || "Reminders"}
      >
        <Calendar size={16} />
        {label}
      </button>
      {open && (
        <ReminderPanel
          activeConvId={activeConvId}
          charName={charName}
          onClose={() => setOpen(false)}
          canCreate={canCreate}
        />
      )}
    </>
  );
}

export function ReminderPanel(props) {
  const { user, sessionGeneration } = useAuth();
  return (
    <ScopedReminderPanel
      key={`${user?.user_id}:${sessionGeneration}:${props.activeConvId}`}
      {...props}
      accountTimezone={user?.timezone}
    />
  );
}

function ScopedReminderPanel({
  activeConvId,
  charName,
  onClose,
  canCreate = false,
  accountTimezone,
}) {
  const { isDark } = useTheme();
  const current = usePanelLifetime();
  const dialogRef = useModalDialog(onClose);

  const [reminders, setReminders] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const [showAddForm, setShowAddForm] = useState(false);
  const [draft, setDraft] = useState(emptyDraft());
  const [isSaving, setIsSaving] = useState(false);
  const [formError, setFormError] = useState(null);

  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState(emptyDraft());

  const [pendingDeleteId, setPendingDeleteId] = useState(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const data = await current(listReminders());
      setReminders(validateReminders(data));
    } catch (err) {
      setLoadError(err.message || "Could not load reminders.");
    } finally {
      setIsLoading(false);
    }
  }, [current]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleAdd = async (e) => {
    e.preventDefault();
    if (isLoading || loadError || isSaving || !canCreate) return;
    if (!activeConvId) {
      setFormError("Open a chat first to add a reminder.");
      return;
    }
    if (!draft.reminderText.trim() || !draft.dueDate || !draft.dueTime) {
      setFormError("Please fill in the reminder, date and time.");
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      const created = validateReminder(
        await current(
          createReminder({
            conversationId: activeConvId,
            reminderText: draft.reminderText.trim(),
            dueDate: draft.dueDate,
            dueTime: draft.dueTime,
          }),
        ),
      );
      setReminders((prev) =>
        [...prev, created].sort((a, b) => a.due_at.localeCompare(b.due_at)),
      );
      setDraft(emptyDraft());
      setShowAddForm(false);
    } catch (err) {
      setFormError(err.message || "Could not create reminder.");
    } finally {
      setIsSaving(false);
    }
  };

  const startEdit = (reminder) => {
    setEditingId(reminder.reminder_id);
    setEditDraft({
      reminderText: reminder.reminder_text,
      dueDate: toDateInputValue(reminder.due_at_local),
      dueTime: toTimeInputValue(reminder.due_at_local),
    });
    setFormError(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft(emptyDraft());
  };

  const handleEditSave = async (reminderId) => {
    if (isSaving) return;
    if (
      !editDraft.reminderText.trim() ||
      !editDraft.dueDate ||
      !editDraft.dueTime
    ) {
      setFormError("Please fill in the reminder, date and time.");
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      const original = reminders.find((r) => r.reminder_id === reminderId);
      if (!original)
        throw new Error(
          "This reminder is no longer available. Reload the list.",
        );
      const updates = {};
      if (editDraft.reminderText.trim() !== original.reminder_text)
        updates.reminderText = editDraft.reminderText.trim();
      if (editDraft.dueDate !== toDateInputValue(original.due_at_local))
        updates.dueDate = editDraft.dueDate;
      if (editDraft.dueTime !== toTimeInputValue(original.due_at_local))
        updates.dueTime = editDraft.dueTime;
      if (!Object.keys(updates).length) {
        cancelEdit();
        return;
      }
      const updated = validateReminder(
        await current(updateReminder(reminderId, updates)),
      );
      setReminders((prev) =>
        prev
          .map((r) => (r.reminder_id === reminderId ? updated : r))
          .sort((a, b) => a.due_at.localeCompare(b.due_at)),
      );
      cancelEdit();
    } catch (err) {
      setFormError(
        err.status === 404
          ? "This reminder was delivered or is no longer available. Reload the list."
          : err.message || "Could not update reminder.",
      );
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (reminderId) => {
    if (isSaving) return;
    setIsSaving(true);
    setFormError(null);
    try {
      await current(deleteReminder(reminderId));
      setReminders((prev) => prev.filter((r) => r.reminder_id !== reminderId));
      setPendingDeleteId(null);
    } catch (err) {
      setFormError(
        err.status === 404
          ? "This reminder was delivered or is no longer available. Reload the list."
          : err.message || "Could not delete reminder.",
      );
    } finally {
      setIsSaving(false);
    }
  };

  const textColor = isDark ? "#f8fafc" : "#0f172a";
  const mutedColor = isDark ? "#94a3b8" : "#64748b";
  const borderColor = isDark ? "rgba(255,255,255,0.08)" : "rgba(15,23,42,0.08)";
  const inputBg = isDark ? "rgba(255,255,255,0.05)" : "rgba(15,23,42,0.03)";

  const inputStyle = {
    background: inputBg,
    border: `1px solid ${borderColor}`,
    color: textColor,
  };

  return (
    <div
      ref={dialogRef}
      className="fixed inset-0 z-[999] flex items-center justify-center p-3 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Reminders"
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
            <Calendar size={18} style={{ color: textColor }} />
            <h2
              className="text-base font-semibold"
              style={{ color: textColor }}
            >
              Reminders
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close reminders"
            className="flex h-8 w-8 items-center justify-center rounded-full transition-all duration-200 hover:scale-105"
            style={{ background: inputBg, color: mutedColor }}
          >
            <X size={17} />
          </button>
        </div>

        {/* Body: scrollable list */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
          {isLoading ? (
            <div
              className="flex items-center justify-center gap-2 py-8 text-sm"
              style={{ color: mutedColor }}
            >
              <Loader2 size={16} className="animate-spin" />
              Loading reminders…
            </div>
          ) : loadError ? (
            <div>
              <p
                role="alert"
                className="py-4 text-center text-sm text-rose-400"
              >
                {loadError}
              </p>
              <button type="button" onClick={refresh}>
                Reload reminders
              </button>
            </div>
          ) : reminders.length === 0 ? (
            <p
              className="py-8 text-center text-sm"
              style={{ color: mutedColor }}
            >
              {canCreate && activeConvId
                ? `No reminders yet. Add one below, or just ask ${charName || "your character"} to remind you of something.`
                : "No scheduled reminders. To create one, enable Reminders in Settings and open a chat."}
            </p>
          ) : (
            reminders.map((reminder) => {
              const isEditing = editingId === reminder.reminder_id;
              const isPendingDelete = pendingDeleteId === reminder.reminder_id;
              return (
                <div
                  key={reminder.reminder_id}
                  className="rounded-xl border p-3"
                  style={{ borderColor, background: inputBg }}
                >
                  {isEditing ? (
                    <div className="space-y-2">
                      <p className="text-xs" style={{ color: mutedColor }}>
                        Timezone: {reminder.timezone || "unavailable"}
                      </p>
                      <textarea
                        value={editDraft.reminderText}
                        onChange={(e) =>
                          setEditDraft((d) => ({
                            ...d,
                            reminderText: e.target.value,
                          }))
                        }
                        rows={2}
                        maxLength={500}
                        className="w-full resize-none rounded-lg px-2.5 py-1.5 text-sm outline-none"
                        style={inputStyle}
                      />
                      <div className="flex gap-2">
                        <input
                          type="date"
                          aria-label={`Reminder date (${reminder.timezone || "timezone unavailable"})`}
                          value={editDraft.dueDate}
                          onChange={(e) =>
                            setEditDraft((d) => ({
                              ...d,
                              dueDate: e.target.value,
                            }))
                          }
                          className="flex-1 rounded-lg px-2.5 py-1.5 text-sm outline-none"
                          style={inputStyle}
                        />
                        <input
                          type="time"
                          aria-label={`Reminder time (${reminder.timezone || "timezone unavailable"})`}
                          value={editDraft.dueTime}
                          onChange={(e) =>
                            setEditDraft((d) => ({
                              ...d,
                              dueTime: e.target.value,
                            }))
                          }
                          className="flex-1 rounded-lg px-2.5 py-1.5 text-sm outline-none"
                          style={inputStyle}
                        />
                      </div>
                      <div className="flex justify-end gap-2 pt-1">
                        <button
                          type="button"
                          onClick={cancelEdit}
                          disabled={isSaving}
                          className="rounded-lg px-3 py-1.5 text-xs font-medium"
                          style={{ color: mutedColor }}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => handleEditSave(reminder.reminder_id)}
                          disabled={isSaving}
                          className="rounded-lg px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                          style={{ background: "#6366f1" }}
                        >
                          Save
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p
                          className="text-sm leading-5 break-words"
                          style={{ color: textColor }}
                        >
                          {reminder.reminder_text}
                        </p>
                        <p
                          className="mt-1 text-xs"
                          style={{ color: mutedColor }}
                        >
                          {reminder.due_at_local} ·{" "}
                          {reminder.timezone || "Timezone unavailable"} ·{" "}
                          {reminder.character_name}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        {isPendingDelete ? (
                          <>
                            <button
                              type="button"
                              onClick={() => setPendingDeleteId(null)}
                              disabled={isSaving}
                              className="rounded-lg px-2 py-1 text-xs font-medium"
                              style={{ color: mutedColor }}
                            >
                              Cancel
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDelete(reminder.reminder_id)}
                              disabled={isSaving}
                              className="rounded-lg bg-rose-500 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                            >
                              Confirm
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              type="button"
                              onClick={() => startEdit(reminder)}
                              disabled={isSaving}
                              aria-label="Edit reminder"
                              className="rounded-full p-1.5 hover:bg-gray-200 dark:hover:bg-gray-800"
                              style={{ color: mutedColor }}
                            >
                              <Pencil size={14} />
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setFormError(null);
                                setPendingDeleteId(reminder.reminder_id);
                              }}
                              disabled={isSaving}
                              aria-label="Delete reminder"
                              className="rounded-full p-1.5 hover:bg-gray-200 dark:hover:bg-gray-800"
                              style={{ color: mutedColor }}
                            >
                              <Trash2 size={14} />
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Footer: add form */}
        <div className="border-t px-4 py-3" style={{ borderColor }}>
          {formError && (
            <div role="alert">
              <p className="text-xs text-rose-400">{formError}</p>
              <button type="button" disabled={isSaving} onClick={refresh}>
                Reload reminders
              </button>
            </div>
          )}
          {!canCreate && (
            <p className="text-xs" style={{ color: mutedColor }}>
              {activeConvId
                ? "New reminders are off. Scheduled reminders remain active; you can edit or cancel them here."
                : "Manage scheduled reminders here, even when the chat icon is hidden. To add a reminder, enable Reminders in Settings and open a chat."}
            </p>
          )}
          {showAddForm && canCreate ? (
            <form onSubmit={handleAdd} className="space-y-2">
              <p className="text-xs" style={{ color: mutedColor }}>
                Timezone:{" "}
                {accountTimezone || "unavailable — refresh your profile"}
              </p>
              <textarea
                value={draft.reminderText}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, reminderText: e.target.value }))
                }
                placeholder={`Remind me to…${charName ? ` (for ${charName})` : ""}`}
                rows={2}
                maxLength={500}
                className="w-full resize-none rounded-lg px-2.5 py-1.5 text-sm outline-none"
                style={inputStyle}
                autoFocus
              />
              <div className="flex gap-2">
                <input
                  type="date"
                  aria-label={`New reminder date (${accountTimezone || "timezone unavailable"})`}
                  value={draft.dueDate}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, dueDate: e.target.value }))
                  }
                  className="flex-1 rounded-lg px-2.5 py-1.5 text-sm outline-none"
                  style={inputStyle}
                />
                <input
                  type="time"
                  aria-label={`New reminder time (${accountTimezone || "timezone unavailable"})`}
                  value={draft.dueTime}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, dueTime: e.target.value }))
                  }
                  className="flex-1 rounded-lg px-2.5 py-1.5 text-sm outline-none"
                  style={inputStyle}
                />
              </div>
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => {
                    setShowAddForm(false);
                    setDraft(emptyDraft());
                    setFormError(null);
                  }}
                  disabled={isSaving || isLoading || Boolean(loadError)}
                  className="rounded-lg px-3 py-1.5 text-xs font-medium"
                  style={{ color: mutedColor }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={
                    isSaving || isLoading || Boolean(loadError) || !canCreate
                  }
                  className="rounded-lg px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                  style={{ background: "#6366f1" }}
                >
                  {isSaving ? "Adding…" : "Add reminder"}
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => {
                setShowAddForm(true);
                setFormError(null);
              }}
              disabled={
                !activeConvId || isLoading || Boolean(loadError) || !canCreate
              }
              data-testid="reminder-add-toggle"
              className="flex w-full items-center justify-center gap-1.5 rounded-lg py-2 text-sm font-medium disabled:opacity-50"
              style={{ background: inputBg, color: textColor }}
              title={
                !activeConvId
                  ? "Open a chat first to add a reminder."
                  : undefined
              }
            >
              <Plus size={15} />
              Add reminder
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
