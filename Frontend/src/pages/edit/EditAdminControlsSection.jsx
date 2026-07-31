import { useLanguage } from "../../context/LanguageContext";
import { TextArea, FieldError } from "../character/CharacterFormFields";
import { STATUS_OPTIONS } from "./constants";

function ToggleRow({
  label,
  description,
  checked,
  onChange,
  disabled = false,
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl border p-4 theme-card">
      <div className="min-w-0">
        <p className="text-sm font-medium theme-text">{label}</p>
        {description && (
          <p className="mt-1 text-xs theme-text-muted">{description}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        disabled={disabled}
        className="relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition disabled:cursor-not-allowed disabled:opacity-60"
        style={{
          backgroundColor: checked
            ? "var(--color-accent)"
            : "var(--color-border)",
        }}
        aria-pressed={checked}
      >
        <span
          className="inline-block h-5 w-5 transform rounded-full bg-white transition"
          style={{ marginLeft: checked ? "1.45rem" : "0.2rem" }}
        />
      </button>
    </div>
  );
}

export default function EditAdminControlsSection({
  form,
  updateField,
  errors,
}) {
  const { t } = useLanguage();

  return (
    <div className="space-y-5 rounded-2xl border p-4 theme-card">
      <div>
        <h3 className="text-base font-semibold theme-text">
          {t("admin.adminControls")}
        </h3>
        <p className="mt-1 text-xs theme-text-muted">
          {t("admin.adminControlsDescription")}
        </p>
      </div>

      <label className="block">
        <span className="mb-2 block text-sm font-medium theme-text">
          {t("admin.reviewStatus")}
        </span>
        <select
          value={form.status}
          onChange={(e) => updateField("status", e.target.value)}
          className="w-full rounded-2xl border px-4 py-3 text-sm theme-card theme-text"
          style={{
            borderColor: errors.status
              ? "rgba(244,63,94,0.6)"
              : "var(--color-border)",
          }}
        >
          {STATUS_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {t(opt.labelKey)}
            </option>
          ))}
        </select>
        <FieldError message={errors.status} />
      </label>

      {form.status === "rejected" && (
        <TextArea
          label={t("admin.rejectionReason")}
          value={form.rejection_reason}
          onChange={(e) => updateField("rejection_reason", e.target.value)}
          rows={4}
          error={errors.rejection_reason}
          required
        />
      )}

      <ToggleRow
        label={t("admin.active")}
        description={t("admin.activeDescription")}
        checked={form.is_active}
        onChange={(v) => updateField("is_active", v)}
        disabled={form.status !== "approved"}
      />
      <ToggleRow
        label={t("admin.featured")}
        description={t("admin.featuredDescription")}
        checked={form.is_featured}
        onChange={(v) => updateField("is_featured", v)}
      />
      <ToggleRow
        label={t("admin.nsfwEnabled")}
        description={t("admin.nsfwEnabledDescription")}
        checked={form.nsfw_enabled}
        onChange={(v) => updateField("nsfw_enabled", v)}
      />
    </div>
  );
}
