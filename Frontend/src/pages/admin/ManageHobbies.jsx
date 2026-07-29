// src/pages/admin/ManageHobbies.jsx

import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { useLanguage } from "../../context/LanguageContext";
import { listHobbies, createHobby } from "../../services/characterService";
import { getCharacterMessage } from "../../utils/characterMessageMapper";
import PageCard from "../../components/PageCard";
import { Loader2, Plus, Trash2, Heart, Check } from "lucide-react";

export default function ManageHobbies() {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { user, isAdmin } = useAuth();

  const [hobbies, setHobbies] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [newHobbyName, setNewHobbyName] = useState("");
  const [newHobbyDescription, setNewHobbyDescription] = useState("");
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

  // Fetch hobbies
  useEffect(() => {
    async function fetchHobbies() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await listHobbies();
        setHobbies(response?.data?.hobbies || []);
      } catch (err) {
        setError(getCharacterMessage(err, "Failed to load hobbies."));
      } finally {
        setIsLoading(false);
      }
    }
    fetchHobbies();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!newHobbyName.trim()) {
      setFormError(t("errors.name_required") || "Name is required.");
      return;
    }

    setIsSubmitting(true);
    setFormError(null);
    setSuccessMessage(null);

    try {
      const response = await createHobby({
        name: newHobbyName.trim(),
        description: newHobbyDescription.trim(),
      });

      const newHobby = response?.data?.hobby;
      if (newHobby) {
        setHobbies((prev) => [...prev, newHobby]);
      }

      setNewHobbyName("");
      setNewHobbyDescription("");
      setSuccessMessage(
        t("errors.hobby_created_successfully") || "Hobby created successfully!",
      );

      setTimeout(() => setSuccessMessage(null), 3000);
    } catch (err) {
      setFormError(getCharacterMessage(err, "Failed to create hobby."));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!user || !isAdmin) return null;

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold theme-text">
          {t("admin.hobbiesTitle") || "Manage Hobbies"}
        </h1>
        <p className="mt-1 text-sm theme-text-muted">
          {t("admin.hobbiesSubtitle") ||
            "Add hobbies that users can select when creating characters (up to 3 per character)."}
        </p>
      </div>

      {/* Add Hobby Form */}
      <PageCard className="mb-6">
        <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold theme-text">
          <Heart size={20} />
          {t("admin.addHobby") || "Add New Hobby"}
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
              value={newHobbyName}
              onChange={(e) => setNewHobbyName(e.target.value)}
              placeholder={t("admin.hobbyNamePlaceholder") || "e.g., Reading"}
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
              value={newHobbyDescription}
              onChange={(e) => setNewHobbyDescription(e.target.value)}
              placeholder={
                t("admin.hobbyDescriptionPlaceholder") ||
                "Brief description of this hobby..."
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
                  {t("admin.addHobby") || "Add Hobby"}
                </>
              )}
            </button>
          </div>
        </form>
      </PageCard>

      {/* Hobbies List – bullet style */}
      <PageCard>
        <h2 className="mb-4 text-lg font-semibold theme-text">
          {t("admin.existingHobbies") || "Existing Hobbies"} ({hobbies.length})
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
        ) : hobbies.length === 0 ? (
          <p className="py-8 text-center text-sm theme-text-muted">
            {t("admin.noHobbies") || "No hobbies yet. Add one above."}
          </p>
        ) : (
          <div className="space-y-2">
            {hobbies.map((hobby) => (
              <div
                key={hobby.id}
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
                    {hobby.name}
                  </h3>
                  {hobby.description && (
                    <p className="mt-1 text-xs theme-text-muted">
                      {hobby.description}
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
