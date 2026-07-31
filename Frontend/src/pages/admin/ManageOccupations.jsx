// src/pages/admin/ManageOccupations.jsx

import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { useLanguage } from "../../context/LanguageContext";
import {
  listOccupations,
  createOccupation,
} from "../../services/characterService";
import { getCharacterMessage } from "../../utils/characterMessageMapper";
import PageCard from "../../components/PageCard";
import { Loader2, Plus, Trash2, Briefcase, Check } from "lucide-react";

export default function ManageOccupations() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { user, isAdmin } = useAuth();

  const [occupations, setOccupations] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [newOccupationName, setNewOccupationName] = useState("");
  const [newOccupationDescription, setNewOccupationDescription] = useState("");
  const [formError, setFormError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);

  // Redirect if not admin
  useEffect(() => {
    if (!user) {
      navigate("/login");
    } else if (!isAdmin) {
      navigate("/characters");
    }
  }, [user, isAdmin, navigate]);

  // Fetch occupations
  useEffect(() => {
    async function fetchOccupations() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await listOccupations();
        setOccupations(response?.data?.occupations || []);
      } catch (err) {
        setError(getCharacterMessage(err, "Failed to load occupations."));
      } finally {
        setIsLoading(false);
      }
    }
    fetchOccupations();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!newOccupationName.trim()) {
      setFormError(t("errors.name_required") || "Name is required.");
      return;
    }

    setIsSubmitting(true);
    setFormError(null);
    setSuccessMessage(null);

    try {
      const response = await createOccupation({
        name: newOccupationName.trim(),
        description: newOccupationDescription.trim(),
      });

      const newOcc = response?.data?.occupation;
      if (newOcc) {
        setOccupations((prev) => [...prev, newOcc]);
      }

      setNewOccupationName("");
      setNewOccupationDescription("");
      setSuccessMessage(
        t("errors.occupation_created_successfully") ||
          "Occupation created successfully!",
      );

      setTimeout(() => setSuccessMessage(null), 3000);
    } catch (err) {
      setFormError(getCharacterMessage(err, "Failed to create occupation."));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!user || !isAdmin) return null;

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold theme-text">
          {t("admin.occupationsTitle") || "Manage Occupations"}
        </h1>
        <p className="mt-1 text-sm theme-text-muted">
          {t("admin.occupationsSubtitle") ||
            "Add occupations that users can select when creating characters."}
        </p>
      </div>

      {/* Add Occupation Form */}
      <PageCard className="mb-6">
        <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold theme-text">
          <Briefcase size={20} />
          {t("admin.addOccupation") || "Add New Occupation"}
        </h2>

        {successMessage && (
          <div className="mb-4 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-300">
            {successMessage}
          </div>
        )}

        {formError && (
          <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
            {formError}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium theme-text">
              {t("wizard.name") || "Name"}{" "}
              <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={newOccupationName}
              onChange={(e) => setNewOccupationName(e.target.value)}
              placeholder={
                t("admin.occupationNamePlaceholder") ||
                "e.g., Software Engineer"
              }
              className="w-full rounded-xl border px-4 py-3 text-sm outline-none transition"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-card)",
                color: "var(--color-text)",
              }}
              maxLength={100}
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium theme-text">
              {t("admin.description") || "Description"}{" "}
              <span className="text-xs theme-text-muted">(optional)</span>
            </label>
            <textarea
              value={newOccupationDescription}
              onChange={(e) => setNewOccupationDescription(e.target.value)}
              placeholder={
                t("admin.occupationDescriptionPlaceholder") ||
                "Brief description of this occupation..."
              }
              rows={2}
              className="w-full rounded-xl border px-4 py-3 text-sm outline-none transition"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-card)",
                color: "var(--color-text)",
              }}
            />
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={isSubmitting}
              className="flex items-center gap-2 rounded-xl px-5 py-2 text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
              style={{
                backgroundColor: "var(--color-accent)",
                color: "#082f49",
              }}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="animate-spin" size={16} />
                  {t("common.creating") || "Creating..."}
                </>
              ) : (
                <>
                  <Plus size={16} />
                  {t("admin.addOccupation") || "Add Occupation"}
                </>
              )}
            </button>
          </div>
        </form>
      </PageCard>

      {/* Occupations List – bullet style */}
      <PageCard>
        <h2 className="mb-4 text-lg font-semibold theme-text">
          {t("admin.existingOccupations") || "Existing Occupations"} (
          {occupations.length})
        </h2>

        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm theme-text-muted">
            <Loader2 className="animate-spin" size={20} />
            {t("common.loading") || "Loading..."}
          </div>
        ) : error ? (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-center text-sm text-red-300">
            {error}
          </div>
        ) : occupations.length === 0 ? (
          <p className="py-8 text-center text-sm theme-text-muted">
            {t("admin.noOccupations") || "No occupations yet. Add one above."}
          </p>
        ) : (
          <div className="space-y-2">
            {occupations.map((occ) => (
              <div
                key={occ.id}
                className="flex items-start gap-3 rounded-xl border p-4 theme-card"
              >
                {/* Check‑mark bullet */}
                <div
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
                  style={{
                    backgroundColor: "var(--color-accent-soft)",
                    color: "var(--color-accent)",
                  }}
                >
                  <Check size={16} />
                </div>

                {/* Name & description */}
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-semibold theme-text">
                    {occ.name}
                  </h3>
                  {occ.description && (
                    <p className="mt-1 text-xs theme-text-muted">
                      {occ.description}
                    </p>
                  )}
                </div>

                {/* Delete button (placeholder for future) */}
                <button
                  type="button"
                  className="ml-2 shrink-0 rounded-lg p-2 text-red-400 opacity-50 transition hover:opacity-100"
                  style={{ cursor: "not-allowed" }}
                  title={t("admin.comingSoon") || "Delete coming soon"}
                  disabled
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        )}
      </PageCard>
    </div>
  );
}
