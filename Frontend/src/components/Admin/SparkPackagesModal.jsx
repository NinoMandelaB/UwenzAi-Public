// src/components/admin/SparkPackagesModal.jsx
import { useState, useEffect, useCallback, useRef } from "react";
import { X, Plus, Pencil, Trash2, Loader2, AlertCircle } from "lucide-react";
import {
  listSparkPackages,
  createSparkPackage,
  updateSparkPackage,
  deleteSparkPackage,
} from "../../services/billingService";
import { useLanguage } from "../../context/LanguageContext";

/* ------------------------------------------------------------------ */
/*  Modal (fixed header, scrollable body) – border changed to gray     */
/* ------------------------------------------------------------------ */
function Modal({ open, onClose, title, children }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="relative flex max-h-[90vh] w-full max-w-xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl">
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
/*  Package list – cards with label / value grid                       */
/* ------------------------------------------------------------------ */
function PackageList({ packages, onEdit, onDelete, t }) {
  if (packages.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-gray-400">
        {t("admin.noSparkPackages")}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {packages.map((pkg) => (
        <div
          key={pkg.slug}
          className="rounded-xl border border-gray-100 bg-white p-4 shadow-sm"
        >
          <div className="mb-3 flex items-center justify-between">
            <h3 className="font-medium text-gray-900">{pkg.name}</h3>
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${
                pkg.is_active ? "bg-green-400" : "bg-gray-300"
              }`}
            />
          </div>

          <div className="grid grid-cols-2 gap-y-2 text-sm">
            <span className="text-gray-500">{t("admin.slug")}</span>
            <span className="text-right text-gray-700">{pkg.slug}</span>

            <span className="text-gray-500">{t("admin.sparks")}</span>
            <span className="text-right tabular-nums text-gray-700">
              {pkg.base_spark_amount}
            </span>

            <span className="text-gray-500">{t("admin.basePrice")}</span>
            <span className="text-right tabular-nums text-gray-700">
              KES {Number(pkg.base_price_kes).toFixed(2)}
            </span>

            <span className="text-gray-500">{t("admin.discount")}</span>
            <span className="text-right tabular-nums text-gray-700">
              {Number(pkg.discount_percent).toFixed(1)}%
            </span>

            <span className="text-gray-500">{t("admin.finalPrice")}</span>
            <span className="text-right tabular-nums font-medium text-gray-900">
              KES {Number(pkg.final_price_kes).toFixed(2)}
            </span>
          </div>

          <div className="mt-3 flex justify-end gap-2 border-t border-gray-200 pt-3">
            <button
              onClick={() => onEdit(pkg)}
              className="rounded-md p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            >
              <Pencil size={15} />
            </button>
            <button
              onClick={() => onDelete(pkg.slug)}
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
  base_spark_amount: "",
  base_price_kes: "",
  discount_percent: "0",
  final_price_kes: "",
  is_active: true,
};

function SparkPackageForm({ initial, onSave, onCancel, saving, t }) {
  const [form, setForm] = useState(emptyForm);

  useEffect(() => {
    if (initial) {
      setForm({
        slug: initial.slug || "",
        name: initial.name || "",
        description: initial.description || "",
        base_spark_amount: initial.base_spark_amount?.toString() || "",
        base_price_kes: initial.base_price_kes?.toString() || "",
        discount_percent: initial.discount_percent?.toString() || "0",
        final_price_kes: initial.final_price_kes?.toString() || "",
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

    setForm((prev) => {
      const updated = { ...prev, [name]: value };

      if (name === "base_price_kes" || name === "discount_percent") {
        const base = parseFloat(
          name === "base_price_kes" ? value : prev.base_price_kes,
        );
        const disc = parseFloat(
          name === "discount_percent" ? value : prev.discount_percent,
        );
        const final =
          isNaN(base) || isNaN(disc) ? 0 : base - (base * disc) / 100;
        updated.final_price_kes = final.toFixed(2);
      }

      return updated;
    });
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    onSave({
      ...form,
      base_spark_amount: parseInt(form.base_spark_amount || "0", 10),
      base_price_kes: parseFloat(form.base_price_kes || "0"),
      discount_percent: parseFloat(form.discount_percent || "0"),
      final_price_kes: parseFloat(form.final_price_kes || "0"),
    });
  };

  const inputClass =
    "w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-200";

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-[140px_1fr] sm:items-center">
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

        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.sparks")}
        </label>
        <SafeNumericInput
          name="base_spark_amount"
          value={form.base_spark_amount}
          onChange={handleChange}
          required
          placeholder={t("admin.sparkAmountPlaceholder")}
          className={inputClass}
        />

        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.basePrice")} (KES)
        </label>
        <SafeNumericInput
          name="base_price_kes"
          value={form.base_price_kes}
          onChange={handleChange}
          required
          allowDecimal
          placeholder={t("admin.basePricePlaceholder")}
          className={inputClass}
        />

        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.discount")} %
        </label>
        <SafeNumericInput
          name="discount_percent"
          value={form.discount_percent}
          onChange={handleChange}
          allowDecimal
          placeholder={t("admin.discountPlaceholder")}
          className={inputClass}
        />

        <label className="text-xs font-medium text-gray-600 sm:text-sm">
          {t("admin.finalPrice")} (KES)
        </label>
        <SafeNumericInput
          name="final_price_kes"
          value={form.final_price_kes}
          onChange={handleChange}
          allowDecimal
          placeholder={t("admin.finalPricePlaceholder")}
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

      <div className="flex justify-end gap-2 pt-2">
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
export default function SparkPackagesModal({ open, onClose }) {
  const { t } = useLanguage();
  const [packages, setPackages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(null);

  const fetchPackages = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listSparkPackages();
      setPackages(res.data.packages || []);
    } catch (e) {
      setError(e.message || "Failed to load packages");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) fetchPackages();
  }, [open, fetchPackages]);

  const handleSave = async (data) => {
    setSaving(true);
    try {
      if (editing) {
        await updateSparkPackage(editing.slug, data);
      } else {
        await createSparkPackage(data);
      }
      setShowForm(false);
      setEditing(null);
      fetchPackages();
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (slug) => {
    await deleteSparkPackage(slug);
    setDeleteConfirm(null);
    fetchPackages();
  };

  const packageCount = packages.length;
  const packageCountText =
    packageCount === 1
      ? t("admin.packageCount").replace("{count}", packageCount)
      : t("admin.packageCountPlural").replace("{count}", packageCount);

  return (
    <Modal open={open} onClose={onClose} title={t("admin.sparkPackages")} t={t}>
      <div className="space-y-4">
        {error && (
          <div className="flex items-center gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-600">
            <AlertCircle size={16} />
            {error}
          </div>
        )}

        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-500">{packageCountText}</span>
          <button
            onClick={() => {
              setEditing(null);
              setShowForm(true);
            }}
            className="flex items-center gap-1 rounded-lg bg-sky-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-600"
          >
            <Plus size={14} />
            {t("admin.addPackage")}
          </button>
        </div>

        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="animate-spin text-gray-400" size={24} />
          </div>
        ) : (
          <PackageList
            packages={packages}
            onEdit={(pkg) => {
              setEditing(pkg);
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
        title={editing ? t("admin.editPackage") : t("admin.newPackage")}
        t={t}
      >
        <SparkPackageForm
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
        title={t("admin.deletePackage")}
        t={t}
      >
        <p className="mb-4 text-sm text-gray-600">
          {t("admin.deletePackageConfirm")}
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
            {t("admin.deletePackage")}
          </button>
        </div>
      </Modal>
    </Modal>
  );
}
