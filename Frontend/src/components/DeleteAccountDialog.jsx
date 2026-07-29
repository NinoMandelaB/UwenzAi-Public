import { useEffect, useState } from "react";
import { Loader2, TriangleAlert, X } from "lucide-react";

const CONFIRMATION_WORDS = [
  "DELETE",
  "REMOVE",
  "CONFIRM",
  "ERASE",
  "DESTROY",
  "PURGE",
  "CLEAR",
  "RESET",
  "WIPE",
  "DISCARD",
  "REVOKE",
  "FORGET",
  "CANCEL",
  "UNLINK",
  "DROP",
  "CLOSE",
  "ARCHIVE",
  "DISABLE",
  "EXPIRE",
  "FINISH",
];

export default function DeleteAccountDialog({
  isOpen,
  isDeleting,
  errorMessage,
  onClose,
  onConfirm,
}) {
  const [requiredText, setRequiredText] = useState("");
  const [confirmationText, setConfirmationText] = useState("");

  useEffect(() => {
    if (isOpen) {
      const randomWord =
        CONFIRMATION_WORDS[
          Math.floor(Math.random() * CONFIRMATION_WORDS.length)
        ];
      setRequiredText(randomWord);
      setConfirmationText("");
      return;
    }

    setRequiredText("");
    setConfirmationText("");
  }, [isOpen]);

  if (!isOpen) return null;

  const isMatch = confirmationText.trim().toUpperCase() === requiredText;

  const handleClose = () => {
    if (isDeleting) return;
    setConfirmationText("");
    onClose();
  };

  const handleConfirm = () => {
    if (isDeleting || !isMatch) return;
    onConfirm();
  };

  return (
    <div
      className="fixed inset-0 z-[140] flex items-end justify-center bg-black/50 px-4 py-4 sm:items-center"
      onClick={isDeleting ? undefined : handleClose}
    >
      <div
        className="w-full max-w-md rounded-[1.75rem] border shadow-2xl"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-card)",
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 p-5">
          <div>
            <h3 className="text-lg font-semibold theme-text">Delete account</h3>
            <p className="mt-1 text-sm theme-text-muted">
              This action permanently deletes your account and all associated
              data.
            </p>
          </div>

          <button
            type="button"
            onClick={handleClose}
            disabled={isDeleting}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            style={{ borderColor: "var(--color-border)" }}
            aria-label="Close delete account dialog"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-5 pb-2">
          <div
            className="rounded-2xl border p-4"
            style={{
              borderColor: "rgba(244, 63, 94, 0.35)",
              backgroundColor: "rgba(244, 63, 94, 0.08)",
            }}
          >
            <div className="flex items-start gap-3">
              <div
                className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
                style={{
                  backgroundColor: "rgba(244, 63, 94, 0.14)",
                  color: "rgb(225, 29, 72)",
                }}
              >
                <TriangleAlert size={18} />
              </div>

              <div className="min-w-0">
                <p className="text-sm font-medium theme-text">
                  This cannot be undone.
                </p>
                <p className="mt-1 text-sm leading-6 theme-text-muted">
                  Your profile, characters, conversations, messages, spark_pool
                  data, and other related records may also be permanently
                  removed.
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="px-5 pt-3">
          <div className="space-y-2">
            <p className="text-sm theme-text-muted">
              To confirm, type{" "}
              <span className="font-semibold text-rose-400">
                {requiredText}
              </span>{" "}
              below.
            </p>

            <input
              type="text"
              value={confirmationText}
              onChange={(event) => setConfirmationText(event.target.value)}
              placeholder={requiredText ? `Type ${requiredText}` : "Type word"}
              disabled={isDeleting}
              autoComplete="off"
              spellCheck={false}
              className="w-full rounded-xl border px-4 py-3 text-sm outline-none transition"
              style={{
                borderColor: isMatch
                  ? "rgba(34, 197, 94, 0.5)"
                  : "var(--color-border)",
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text)",
              }}
            />
          </div>
        </div>

        {errorMessage ? (
          <div className="px-5 pt-3">
            <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3">
              <p className="text-sm text-rose-300">{errorMessage}</p>
            </div>
          </div>
        ) : null}

        <div className="flex flex-col gap-3 p-5 sm:flex-row">
          <button
            type="button"
            onClick={handleClose}
            disabled={isDeleting}
            className="inline-flex flex-1 items-center justify-center rounded-xl px-4 py-3 text-sm font-medium transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            style={{
              backgroundColor: "var(--color-soft)",
              color: "var(--color-text)",
              border: "1px solid var(--color-border)",
            }}
          >
            Cancel
          </button>

          <button
            type="button"
            onClick={handleConfirm}
            disabled={isDeleting || !isMatch}
            className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            style={{
              backgroundColor: "rgb(225, 29, 72)",
              border: "1px solid rgba(251, 113, 133, 0.45)",
            }}
          >
            {isDeleting && <Loader2 size={16} className="animate-spin" />}
            {isDeleting ? "Deleting account..." : "Delete account permanently"}
          </button>
        </div>
      </div>
    </div>
  );
}
