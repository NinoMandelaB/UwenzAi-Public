// src/pages/admin/ManagePersonalities.jsx

import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { useLanguage } from "../../context/LanguageContext";
import {
  listPersonalities,
  createPersonality,
  deletePersonality,
} from "../../services/personalityService";
import { getCharacterMessage } from "../../utils/characterMessageMapper";
import PageCard from "../../components/PageCard";
import { Loader2, Plus, Trash2, Sparkles, Check } from "lucide-react";

export default function ManagePersonalities() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { user, isAdmin } = useAuth();

  const [personalities, setPersonalities] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDeleting, setIsDeleting] = useState(null);
  const [newPersonalityName, setNewPersonalityName] = useState("");
  const [newPersonalityDescription, setNewPersonalityDescription] =
    useState("");
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

  // Fetch personalities
  useEffect(() => {
    async function fetchPersonalities() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await listPersonalities();
        setPersonalities(response?.data?.personalities || []);
      } catch (err) {
        setError(getCharacterMessage(err, "Failed to load personalities."));
      } finally {
        setIsLoading(false);
      }
    }
    fetchPersonalities();
  }, []);

  const handleDelete = async (id) => {
    setIsDeleting(id);
    try {
      await deletePersonality(id);
      setPersonalities((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      setError(getCharacterMessage(err, "Failed to delete personality."));
    } finally {
      setIsDeleting(null);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!newPersonalityName.trim()) {
      setFormError(t("errors.name_required") || "Name is required.");
      return;
    }

    setIsSubmitting(true);
    setFormError(null);
    setSuccessMessage(null);

    try {
      const response = await createPersonality(
        newPersonalityName.trim(),
        newPersonalityDescription.trim(),
      );
      const newPers = response?.data;
      if (newPers) {
        setPersonalities((prev) => [
          ...prev,
          {
            id: newPers.id,
            name: newPers.name,
            description: newPersonalityDescription.trim() || "",
          },
        ]);
      }

      setNewPersonalityName("");
      setNewPersonalityDescription("");
      setSuccessMessage(
        t("admin.personalityCreated") || "Personality created successfully!",
      );

      setTimeout(() => setSuccessMessage(null), 3000);
    } catch (err) {
      setFormError(getCharacterMessage(err, "Failed to create personality."));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!user || !isAdmin) return null;

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold theme-text">
          {t("admin.personalitiesTitle") || "Manage Personalities"}
        </h1>
        <p className="mt-1 text-sm theme-text-muted">
          {t("admin.personalitiesSubtitle") ||
            "Add personality traits that users can select when creating characters."}
        </p>
      </div>

      {/* Add Personality Form */}
      <PageCard className="mb-6">
        <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold theme-text">
          <Sparkles size={20} />
          {t("admin.addPersonality") || "Add New Personality"}
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
              value={newPersonalityName}
              onChange={(e) => setNewPersonalityName(e.target.value)}
              placeholder={
                t("admin.personalityNamePlaceholder") ||
                "e.g., Witty, Adventurous"
              }
              className="w-full rounded-xl border px-4 py-3 text-sm outline-none transition"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-card)",
                color: "var(--color-text)",
              }}
              maxLength={50}
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium theme-text">
              {t("admin.description") || "Description"}{" "}
              <span className="text-xs theme-text-muted">(optional)</span>
            </label>
            <textarea
              value={newPersonalityDescription}
              onChange={(e) => setNewPersonalityDescription(e.target.value)}
              placeholder={
                t("admin.personalityDescriptionPlaceholder") ||
                "Brief description of this personality trait..."
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
                  {t("admin.addPersonality") || "Add Personality"}
                </>
              )}
            </button>
          </div>
        </form>
      </PageCard>

      {/* Personality List */}
      <PageCard>
        <h2 className="mb-4 text-lg font-semibold theme-text">
          {t("admin.existingPersonalities") || "Existing Personalities"} (
          {personalities.length})
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
        ) : personalities.length === 0 ? (
          <p className="py-8 text-center text-sm theme-text-muted">
            {t("admin.noPersonalities") ||
              "No personalities yet. Add one above."}
          </p>
        ) : (
          <div className="space-y-2">
            {personalities.map((pers) => (
              <div
                key={pers.id}
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
                    {pers.name}
                  </h3>
                  {pers.description && (
                    <p className="mt-1 text-xs theme-text-muted">
                      {pers.description}
                    </p>
                  )}
                </div>

                {/* Delete button */}
                <button
                  type="button"
                  onClick={() => handleDelete(pers.id)}
                  disabled={isDeleting === pers.id}
                  className="ml-2 shrink-0 rounded-lg p-2 text-red-400 transition hover:bg-red-500/20 hover:text-red-300 disabled:opacity-50"
                >
                  {isDeleting === pers.id ? (
                    <Loader2 className="animate-spin" size={16} />
                  ) : (
                    <Trash2 size={16} />
                  )}
                </button>
              </div>
            ))}
          </div>
        )}
      </PageCard>
    </div>
  );
}
