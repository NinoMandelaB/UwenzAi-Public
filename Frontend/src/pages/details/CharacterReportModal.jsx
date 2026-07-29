import { X } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";

export default function CharacterReportModal({
  show,
  reason,
  error,
  submitting,
  onReasonChange,
  onSubmit,
  onClose,
}) {
  const { t } = useLanguage();

  if (!show) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-2xl p-6 shadow-lg theme-card">
        <div className="flex items-start justify-between mb-4">
          <h3 className="text-lg font-semibold theme-text">
            {t("characters.reportCharacter")}
          </h3>
          <button
            onClick={onClose}
            className="rounded-full p-1 text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
          >
            <X size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <p className="text-sm theme-text-muted">
            {t("characters.reportDescription")}
          </p>
          <textarea
            autoFocus
            rows={4}
            value={reason}
            onChange={(e) => onReasonChange(e.target.value)}
            placeholder={t("characters.reportPlaceholder")}
            className="w-full rounded-xl border px-4 py-3 text-sm theme-text bg-transparent resize-none"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-input)",
            }}
          />
          {error && (
            <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-2 text-sm text-rose-300">
              {error}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="rounded-xl px-4 py-2 text-sm font-medium theme-text-muted hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={onSubmit}
            disabled={!reason.trim() || submitting}
            className="rounded-xl px-4 py-2 text-sm font-medium bg-[var(--color-accent)] text-[#082f49] disabled:opacity-50"
          >
            {submitting
              ? t("characters.sending")
              : t("characters.submitReport")}
          </button>
        </div>
      </div>
    </div>
  );
}
