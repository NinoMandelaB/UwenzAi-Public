// src/components/admin/SubscriptionPlansModal.jsx
import { useState, useEffect, useCallback, useRef } from "react";
import { X, Plus, Pencil, Trash2, Loader2, AlertCircle } from "lucide-react";
import {
  listSubscriptionPlans,
  createSubscriptionPlan,
  updateSubscriptionPlan,
  deleteSubscriptionPlan,
} from "../../services/billingService";
import { useLanguage } from "../../context/LanguageContext";

/* ------------------------------------------------------------------ */
/*  Modal (fixed header, scrollable body) – border changed to gray     */
/* ------------------------------------------------------------------ */
function Modal({ open, onClose, title, children }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="relative flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl">
        <div className="flex shrink-0 items-center justify-between border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
          >
            <X size={20} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Plan list – cards with label / value grid                          */
/* ------------------------------------------------------------------ */
function PlanList({ plans, onEdit, onDelete, t }) {
  if (plans.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-gray-400">
        {t("admin.noSubscriptionPlans")}
      </p>
    );
  }

  const formatCycle = (cycle) => {
    const cycleMap = {
      daily: t("admin.daily"),
      weekly: t("admin.weekly"),
      monthly: t("admin.monthly"),
      yearly: t("admin.yearly"),
    };
    return cycleMap[cycle] || cycle;
  };

  return (
    <div className="space-y-3">
      {plans.map((plan) => (
        <div
          key={plan.slug}
          className="rounded-xl border border-gray-100 bg-white p-4 shadow-sm"
        >
          <div className="mb-3 flex items-center justify-between">
            <h3 className="font-medium text-gray-900">{plan.name}</h3>
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${
                plan.is_active ? "bg-green-400" : "bg-gray-300"
              }`}
            />
          </div>

          <div className="grid grid-cols-2 gap-y-2 text-sm">
            <span className="text-gray-500">{t("admin.slug")}</span>
            <span className="text-right text-gray-700">{plan.slug}</span>

            <span className="text-gray-500">{t("admin.price")}</span>
            <span className="text-right tabular-nums text-gray-700">
              {plan.price} {plan.currency}
            </span>

            <span className="text-gray-500">{t("admin.cycle")}</span>
            <span className="text-right capitalize text-gray-700">
              {formatCycle(plan.billing_cycle)}
            </span>

            <span className="text-gray-500">{t("admin.sparks")}</span>
            <span className="text-right tabular-nums text-gray-700">
              {plan.included_sparks}
            </span>

            <span className="text-gray-500">{t("admin.msgPairs")}</span>
            <span className="text-right tabular-nums text-gray-700">
              {plan.included_message_pairs ?? "∞"}
            </span>

            <span className="text-gray-500">{t("admin.characters")}</span>
            <span className="text-right tabular-nums text-gray-700">
              {plan.included_characters ?? "∞"}
            </span>

            <span className="text-gray-500">{t("admin.images")}</span>
            <span className="text-right tabular-nums text-gray-700">
              {plan.included_images ?? "∞"}
            </span>
          </div>

          <div className="mt-3 flex justify-end gap-2 border-t border-gray-200 pt-3">
            <button
              onClick={() => onEdit(plan)}
              className="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            >
              <Pencil size={15} />
            </button>
            <button
              onClick={() => onDelete(plan.slug)}
              className="rounded-md p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-500"
            >
              <Trash2 size={15} />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Safe Numeric Input – blocks everything except digits / dot         */
/* ------------------------------------------------------------------ */
function SafeNumericInput({ allowDecimal = false, ...props }) {
  const inputRef = useRef(null);

  const handleKeyDown = (e) => {
    const allowedKeys = [
      "Backspace",
      "Delete",
      "ArrowLeft",
      "ArrowRight",
      "Tab",
      "Home",
      "End",
    ];

    if (
      (e.ctrlKey || e.metaKey) &&
      ["a", "c", "v", "x"].includes(e.key.toLowerCase())
    ) {
      return;
    }

    if (allowedKeys.includes(e.key)) return;

    if (allowDecimal && e.key === ".") {
      if ((e.target.value || "").includes(".")) {
        e.preventDefault();
      }
      return;
    }

    if (!/\d/.test(e.key)) {
      e.preventDefault();
    }
  };

  const handlePaste = (e) => {
    e.preventDefault();
    const pasted = (e.clipboardData || window.clipboardData).getData("text");
    const cleaned = allowDecimal
      ? pasted.replace(/[^0-9.]/g, "").replace(/(\..*)\./g, "$1")
      : pasted.replace(/\D/g, "");
    const target = e.target;
    const start = target.selectionStart;
    const end = target.selectionEnd;
    const current = target.value || "";
    const newValue = current.slice(0, start) + cleaned + current.slice(end);
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    ).set;
    nativeInputValueSetter.call(target, newValue);
    target.dispatchEvent(new Event("input", { bubbles: true }));
  };

  return (
    <input
      ref={inputRef}
      type="text"
      inputMode={allowDecimal ? "decimal" : "numeric"}
      onKeyDown={handleKeyDown}
      onPaste={handlePaste}
      {...props}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Form (hint placeholders added to all inputs)                       */
/* ------------------------------------------------------------------ */
const emptyForm = {
  slug: "",
  name: "",
  description: "",
  price: "",
  currency: "KES",
  billing_cycle: "monthly",
  daily_token_limit: "0",
  total_token_limit: "0",
  included_sparks: "0",
  max_tokens_per_request: "0",
  included_message_pairs: "",
  included_characters: "",
  included_images: "",
  fair_use_message_cap: "",
  fair_use_character_cap: "",
  fair_use_image_cap: "",
  is_active: true,
};

function SubscriptionPlanForm({ initial, onSave, onCancel, saving, t }) {
  const [form, setForm] = useState(emptyForm);

  useEffect(() => {
    if (initial) {
      setForm({
        slug: initial.slug || "",
        name: initial.name || "",
        description: initial.description || "",
        price: initial.price?.toString() || "",
        currency: initial.currency || "KES",
        billing_cycle: initial.billing_cycle || "monthly",
        daily_token_limit: initial.daily_token_limit?.toString() || "0",
        total_token_limit: initial.total_token_limit?.toString() || "0",
        included_sparks: initial.included_sparks?.toString() || "0",
        max_tokens_per_request:
          initial.max_tokens_per_request?.toString() || "0",
        included_message_pairs:
          initial.included_message_pairs?.toString() || "",
        included_characters: initial.included_characters?.toString() || "",
        included_images: initial.included_images?.toString() || "",
        fair_use_message_cap: initial.fair_use_message_cap?.toString() || "",
        fair_use_character_cap:
          initial.fair_use_character_cap?.toString() || "",
        fair_use_image_cap: initial.fair_use_image_cap?.toString() || "",
        is_active: initial.is_active ?? true,
      });
    } else {
      setForm(emptyForm);
    }
  }, [initial]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    if (type === "checkbox") {
      setForm((prev) => ({ ...prev, [name]: checked }));
      return;
    }
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    const toInt = (v) => (v === "" ? null : parseInt(v, 10));
    const toFloat = (v) => parseFloat(v) || 0;

    onSave({
      ...form,
      price: toFloat(form.price),
      daily_token_limit: toInt(form.daily_token_limit) || 0,
      total_token_limit: toInt(form.total_token_limit) || 0,
      included_sparks: toInt(form.included_sparks) || 0,
      max_tokens_per_request: toInt(form.max_tokens_per_request) || 0,
      included_message_pairs: toInt(form.included_message_pairs),
      included_characters: toInt(form.included_characters),
      included_images: toInt(form.included_images),
      fair_use_message_cap: toInt(form.fair_use_message_cap),
      fair_use_character_cap: toInt(form.fair_use_character_cap),
      fair_use_image_cap: toInt(form.fair_use_image_cap),
    });
  };

  const inputClass =
    "w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-200";

  const cycleOptions = [
    { value: "daily", label: t("admin.daily") },
    { value: "weekly", label: t("admin.weekly") },
    { value: "monthly", label: t("admin.monthly") },
    { value: "yearly", label: t("admin.yearly") },
  ];

  const currencyOptions = [
    { value: "KES", label: "KES" },
    { value: "USD", label: "USD" },
    { value: "EUR", label: "EUR" },
  ];

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-[160px_1fr] sm:items-center">
        {/* Name */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("characters.name")}
        </label>
        <input
          name="name"
          value={form.name}
          onChange={handleChange}
          required
          placeholder={t("admin.namePlaceholder")}
          className={inputClass}
        />

        {/* Slug */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.slug")}
        </label>
        <input
          name="slug"
          value={form.slug}
          onChange={handleChange}
          required
          placeholder={t("admin.slugPlaceholder")}
          className={inputClass}
        />

        {/* Description */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.description")}
        </label>
        <input
          name="description"
          value={form.description}
          onChange={handleChange}
          placeholder={t("admin.descriptionPlaceholder")}
          className={inputClass}
        />

        {/* Price */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.price")}
        </label>
        <SafeNumericInput
          name="price"
          value={form.price}
          onChange={handleChange}
          required
          allowDecimal
          placeholder={t("admin.pricePlaceholder")}
          className={inputClass}
        />

        {/* Currency */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.currency")}
        </label>
        <select
          name="currency"
          value={form.currency}
          onChange={handleChange}
          className={inputClass}
        >
          {currencyOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>

        {/* Billing Cycle */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.billingCycle")}
        </label>
        <select
          name="billing_cycle"
          value={form.billing_cycle}
          onChange={handleChange}
          className={inputClass}
        >
          {cycleOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>

        {/* Included Sparks */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.includedSparks")}
        </label>
        <SafeNumericInput
          name="included_sparks"
          value={form.included_sparks}
          onChange={handleChange}
          placeholder={t("admin.sparkAmountPlaceholder")}
          className={inputClass}
        />

        {/* Daily Token Limit */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.dailyTokenLimit")}
        </label>
        <SafeNumericInput
          name="daily_token_limit"
          value={form.daily_token_limit}
          onChange={handleChange}
          placeholder="e.g. 100000"
          className={inputClass}
        />

        {/* Total Token Limit */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.totalTokenLimit")}
        </label>
        <SafeNumericInput
          name="total_token_limit"
          value={form.total_token_limit}
          onChange={handleChange}
          placeholder="e.g. 500000"
          className={inputClass}
        />

        {/* Max Tokens / Request */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.maxTokensPerRequest")}
        </label>
        <SafeNumericInput
          name="max_tokens_per_request"
          value={form.max_tokens_per_request}
          onChange={handleChange}
          placeholder="e.g. 4096"
          className={inputClass}
        />

        {/* Included Msg Pairs */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.includedMsgPairs")}
        </label>
        <SafeNumericInput
          name="included_message_pairs"
          value={form.included_message_pairs}
          onChange={handleChange}
          placeholder={t("admin.unlimitedHint")}
          className={inputClass}
        />

        {/* Fair-use Msg Cap */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.fairUseMsgCap")}
        </label>
        <SafeNumericInput
          name="fair_use_message_cap"
          value={form.fair_use_message_cap}
          onChange={handleChange}
          placeholder={t("admin.onlyIfUnlimitedHint")}
          className={inputClass}
        />

        {/* Included Characters */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.includedCharacters")}
        </label>
        <SafeNumericInput
          name="included_characters"
          value={form.included_characters}
          onChange={handleChange}
          placeholder={t("admin.unlimitedHint")}
          className={inputClass}
        />

        {/* Fair-use Char Cap */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.fairUseCharCap")}
        </label>
        <SafeNumericInput
          name="fair_use_character_cap"
          value={form.fair_use_character_cap}
          onChange={handleChange}
          placeholder={t("admin.onlyIfUnlimitedHint")}
          className={inputClass}
        />

        {/* Included Images */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.includedImages")}
        </label>
        <SafeNumericInput
          name="included_images"
          value={form.included_images}
          onChange={handleChange}
          placeholder={t("admin.unlimitedHint")}
          className={inputClass}
        />

        {/* Fair-use Img Cap */}
        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.fairUseImgCap")}
        </label>
        <SafeNumericInput
          name="fair_use_image_cap"
          value={form.fair_use_image_cap}
          onChange={handleChange}
          placeholder={t("admin.onlyIfUnlimitedHint")}
          className={inputClass}
        />
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          name="is_active"
          type="checkbox"
          checked={form.is_active}
          onChange={handleChange}
        />
        {t("admin.active")}
      </label>

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-4 py-2 text-sm text-gray-600 hover:bg-gray-100"
        >
          {t("common.cancel")}
        </button>
        <button
          type="submit"
          disabled={saving}
          className="rounded-lg bg-sky-500 px-4 py-2 text-sm font-medium text-white hover:bg-sky-600 disabled:opacity-50"
        >
          {saving ? t("admin.saving") : t("admin.save")}
        </button>
      </div>
    </form>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                    */
/* ------------------------------------------------------------------ */
export default function SubscriptionPlansModal({ open, onClose }) {
  const { t } = useLanguage();
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(null);

  const fetchPlans = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listSubscriptionPlans();
      setPlans(res.data.plans || []);
    } catch (e) {
      setError(e.message || "Failed to load plans");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) fetchPlans();
  }, [open, fetchPlans]);

  const handleSave = async (data) => {
    setSaving(true);
    try {
      if (editing) {
        await updateSubscriptionPlan(editing.slug, data);
      } else {
        await createSubscriptionPlan(data);
      }
      setShowForm(false);
      setEditing(null);
      fetchPlans();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (slug) => {
    await deleteSubscriptionPlan(slug);
    setDeleteConfirm(null);
    fetchPlans();
  };

  const planCount = plans.length;
  const planCountText =
    planCount === 1
      ? t("admin.planCount").replace("{count}", planCount)
      : t("admin.planCountPlural").replace("{count}", planCount);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("admin.subscriptionPlans")}
      t={t}
    >
      <div className="space-y-4">
        {error && (
          <div className="flex items-center gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-600">
            <AlertCircle size={16} />
            {error}
          </div>
        )}

        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-500">{planCountText}</span>
          <button
            onClick={() => {
              setEditing(null);
              setShowForm(true);
            }}
            className="flex items-center gap-1 rounded-lg bg-sky-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600"
          >
            <Plus size={14} />
            {t("admin.addPlan")}
          </button>
        </div>

        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="animate-spin text-gray-400" size={24} />
          </div>
        ) : (
          <PlanList
            plans={plans}
            onEdit={(plan) => {
              setEditing(plan);
              setShowForm(true);
            }}
            onDelete={(slug) => setDeleteConfirm(slug)}
            t={t}
          />
        )}
      </div>

      {/* Create / Edit modal */}
      <Modal
        open={showForm}
        onClose={() => {
          setShowForm(false);
          setEditing(null);
        }}
        title={editing ? t("admin.editPlan") : t("admin.newPlan")}
        t={t}
      >
        <SubscriptionPlanForm
          initial={editing}
          onSave={handleSave}
          onCancel={() => {
            setShowForm(false);
            setEditing(null);
          }}
          saving={saving}
          t={t}
        />
      </Modal>

      {/* Delete confirmation */}
      <Modal
        open={!!deleteConfirm}
        onClose={() => setDeleteConfirm(null)}
        title={t("admin.deletePlan")}
        t={t}
      >
        <p className="mb-4 text-sm text-gray-600">
          {t("admin.deletePlanConfirm")}
        </p>
        <div className="flex justify-end gap-2">
          <button
            onClick={() => setDeleteConfirm(null)}
            className="rounded-lg px-4 py-2 text-sm text-gray-600 hover:bg-gray-100"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={() => handleDelete(deleteConfirm)}
            className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white hover:bg-red-600"
          >
            {t("admin.deletePlan")}
          </button>
        </div>
      </Modal>
    </Modal>
  );
}
