// src/pages/edit/index.jsx

import { useEffect, useMemo, useRef, useState } from "react";
import { clearOperationId, reuseOperationId } from "../../utils/idempotency";
import { useNavigate, useParams } from "react-router-dom";
import { Loader2, X, Info } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";

import PrimaryButton from "../../components/PrimaryButton";
import { useAuth } from "../../context/AuthContext";
import {
  getCharacterById,
  getCharacterBySlug,
  updateCharacter,
  listGalleryImages,
  listOccupations,
  listHobbies,
} from "../../services/characterService";
import { getCharacterMessage } from "../../utils/characterMessageMapper";
import { INITIAL_FORM } from "../character/characterFormConstants";
import { validateCharacterForm } from "../character/characterFormValidation";

import EditAvatarSection from "./EditAvatarSection";
import EditBasicInfoSection from "./EditBasicInfoSection";
import EditWizardFieldsSection from "./EditWizardFieldsSection";
import EditPersonalitySection from "./EditPersonalitySection";
import EditGallerySection from "./EditGallerySection";
import EditAdminControlsSection from "./EditAdminControlsSection";
import { mapCharacterToForm, looksLikeUuid } from "./utils";
import { getReviewSubmission } from "./reviewSubmission";

export default function CharacterEdit() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const params = useParams();
  const {
    isAuthenticated,
    isAdmin,
    user,
    isLoading: isAuthLoading,
  } = useAuth();

  const [form, setForm] = useState({ ...INITIAL_FORM });
  const [previewUrl, setPreviewUrl] = useState("");
  const [errors, setErrors] = useState({});
  const [submitError, setSubmitError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Re-review is billed, so the resubmit key survives a failed submit.
  const updateOperationRef = useRef(null);

  const [character, setCharacter] = useState(null);
  // isOwner: true when the logged-in user created this character (and is not admin)
  const [isOwner, setIsOwner] = useState(false);
  const [isLoadingCharacter, setIsLoadingCharacter] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [galleryImages, setGalleryImages] = useState([]);
  const [occupations, setOccupations] = useState([]);
  const [hobbiesList, setHobbiesList] = useState([]);

  // ----- Prompt modal state -----
  const [showPromptModal, setShowPromptModal] = useState(false);
  const [promptUsed, setPromptUsed] = useState("");

  const handleImageGenerated = (prompt) => {
    setPromptUsed(prompt);
    setShowPromptModal(true);
  };

  const routeIdentifier =
    params.characterId || params.slug || params.id || params.character || "";

  // ---------- Derived: does saving this character send it to admin review? ----------
  // For a non-admin owner of a public character, saving is the submission:
  // approved characters are re-reviewed, drafts and rejected ones enter the
  // queue. Without this a public draft saved from here stayed hidden forever.
  const review = getReviewSubmission({
    isAdmin,
    isOwner,
    reviewStatus: character?.review_status,
    isPublic: form.is_public,
  });

  // ---------- validation + missing fields ----------
  const { canSubmit, missingFields } = useMemo(() => {
    const hasAvatar = Boolean(form.avatar || previewUrl);
    const failures = [];

    if (!form.name?.trim()) failures.push(t("characters.name"));
    if (!form.short_description?.trim())
      failures.push(t("characters.shortDescription"));
    if (!form.mood?.trim()) failures.push(t("characters.mood"));
    if (!form.intro?.trim()) failures.push(t("characters.intro"));
    if (!form.personality_description?.trim())
      failures.push(t("characters.personalityDescription"));
    if (!form.prompt_template?.trim())
      failures.push(t("characters.promptTemplate"));
    if (!form.greeting_message?.trim())
      failures.push(t("characters.greetingMessage"));
    if (!form.tone_style?.trim()) failures.push(t("characters.toneStyle"));
    if (!form.primary_language?.trim())
      failures.push(t("characters.primaryLanguage"));
    if (!form.category?.trim()) failures.push(t("characters.category"));
    // Only require status field for admins (non-admins cannot set it)
    if (isAdmin && !form.status?.trim()) failures.push(t("characters.status"));
    if (!hasAvatar) failures.push(t("characters.avatar"));

    return {
      canSubmit: failures.length === 0,
      missingFields: failures,
    };
  }, [form, previewUrl, isAdmin, t]);

  useEffect(() => {
    if (isAuthLoading) return;
    if (!isAuthenticated) {
      navigate("/login");
      return;
    }

    let isMounted = true;

    async function fetchData() {
      setIsLoadingCharacter(true);
      setLoadError("");
      try {
        const response = looksLikeUuid(routeIdentifier)
          ? await getCharacterById(routeIdentifier)
          : await getCharacterBySlug(routeIdentifier);

        const nextCharacter = response?.data?.character || null;
        if (!isMounted) return;

        if (!nextCharacter) {
          setLoadError(t("characters.characterNotFound"));
          setCharacter(null);
          return;
        }

        // ---- Permission check (after we have the character) ----
        const createdByUserId =
          nextCharacter.created_by?.user_id ||
          nextCharacter.created_by_user_id ||
          null;
        const ownerCheck =
          Boolean(user?.user_id) && createdByUserId === user.user_id;

        if (!isAdmin && !ownerCheck) {
          navigate("/characters", {
            state: { errorMessage: t("characters.noEditPermission") },
            replace: true,
          });
          return;
        }
        // --------------------------------------------------------

        setCharacter(nextCharacter);
        setIsOwner(ownerCheck && !isAdmin);
        setForm(mapCharacterToForm(nextCharacter));
        setPreviewUrl(nextCharacter.avatar_url || "");

        const [galleryRes, occRes, hobRes] = await Promise.all([
          listGalleryImages(nextCharacter.character_id),
          listOccupations(),
          listHobbies(),
        ]);
        setGalleryImages(galleryRes?.data?.images || []);
        setOccupations(occRes?.data?.occupations || []);
        setHobbiesList(hobRes?.data?.hobbies || []);
      } catch (error) {
        if (!isMounted) return;
        setLoadError(getCharacterMessage(error, t("characters.loadFailed")));
      } finally {
        if (isMounted) setIsLoadingCharacter(false);
      }
    }

    if (routeIdentifier) fetchData();
    else {
      setIsLoadingCharacter(false);
      setLoadError(t("characters.characterNotFound"));
    }
    return () => {
      isMounted = false;
    };
  }, [
    routeIdentifier,
    isAuthenticated,
    isAdmin,
    isAuthLoading,
    user,
    navigate,
    t,
  ]);

  const updateField = (field, value) => {
    setForm((prev) => {
      const next = { ...prev, [field]: value };
      if (field === "status") {
        if (value === "archived" || value === "pending" || value === "rejected")
          next.is_active = false;
        if (value !== "rejected") next.rejection_reason = "";
      }
      return next;
    });
    setErrors((prev) => ({ ...prev, [field]: "" }));
    setSubmitError("");
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!isAuthenticated || (!isAdmin && !isOwner)) {
      setSubmitError(t("characters.noEditPermission"));
      return;
    }

    const nextErrors = validateCharacterForm(form, {
      requireAvatar: !previewUrl,
    });
    // Rejection reason only required for admins setting rejected status
    if (
      isAdmin &&
      form.status === "rejected" &&
      !form.rejection_reason.trim()
    ) {
      nextErrors.rejection_reason = t("characters.rejectionReasonRequired");
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;
    if (!character?.character_id) {
      setSubmitError(t("characters.characterNotFound"));
      return;
    }

    try {
      setIsSubmitting(true);
      setSubmitError("");

      const payload = {
        name: form.name.trim(),
        short_description: form.short_description.trim(),
        mood: form.mood.trim(),
        category: form.category.trim(),
        intro: form.intro.trim(),
        personality_description: form.personality_description.trim(),
        prompt_template: form.prompt_template.trim(),
        system_instructions: form.system_instructions.trim(),
        greeting_message: form.greeting_message.trim(),
        tone_style: form.tone_style.trim(),
        primary_language: form.primary_language.trim(),

        gender: form.gender,
        visual_style: form.visual_style,
        country: form.country.trim(),
        town: form.town.trim(),
        age: form.age,
        skin_color: form.skin_color,
        eye_color: form.eye_color,
        hair_length: form.hair_length,
        hairstyle: form.hairstyle,
        hair_color: form.hair_color,
        body_type: form.body_type,
        breast_size: form.breast_size,
        butt_size: form.butt_size,
        chest_hair: form.chest_hair,
        muscle_definition: form.muscle_definition,
        beard_style: form.beard_style,
        personality_ids: form.personality_ids,
        occupation_id: form.occupation_id,
        hobby_ids: form.hobby_ids,
        relationship_style: form.relationship_style,
        clothing_style: form.clothing_style,
      };

      if (isAdmin) {
        // Admins can freely change status, active flag, featured, nsfw
        payload.status = form.status.trim();
        payload.is_active = form.is_active;
        payload.is_featured = form.is_featured;
        payload.is_public = form.is_public;
        payload.nsfw_enabled = form.nsfw_enabled;
        payload.rejection_reason =
          form.status === "rejected" ? form.rejection_reason.trim() : "";
      } else {
        // The owner may switch the character between public and private; the
        // server moves it in or out of the review queue accordingly.
        payload.is_public = form.is_public;
        if (review.submitForReview) {
          // approved: backend sets status → pending and takes the character
          // offline until an admin reviews the changes.
          // draft / rejected: saving is what submits it for review.
          payload.submit_for_review = true;
        }
      }

      if (form.avatar) payload.avatar = form.avatar;

      await updateCharacter(
        character.character_id,
        payload,
        reuseOperationId(updateOperationRef, character.character_id),
      );
      clearOperationId(updateOperationRef);
      navigate(`/characters/${character.character_id}`, {
        state: {
          successMessage: review.submitForReview
            ? t("characters.updateSuccessPendingReview")
            : t("characters.updateSuccess"),
        },
      });
    } catch (error) {
      setSubmitError(getCharacterMessage(error, t("characters.updateFailed")));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isAuthLoading || isLoadingCharacter) {
    return (
      <div className="space-y-4 pb-10">
        <h2 className="text-xl font-semibold theme-text">
          {t("characters.editCharacter")}
        </h2>
        <div className="flex items-center justify-center rounded-2xl border p-8 theme-card">
          <Loader2 className="animate-spin theme-text" size={18} />
          <p className="ml-3 text-sm theme-text">{t("common.loading")}</p>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="space-y-4 pb-10">
        <h2 className="text-xl font-semibold theme-text">
          {t("characters.editCharacter")}
        </h2>
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4">
          <p className="text-sm text-rose-300">{loadError}</p>
        </div>
        <PrimaryButton onClick={() => navigate(-1)}>
          {t("common.back")}
        </PrimaryButton>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <section>
        <h2 className="text-xl font-semibold theme-text">
          {t("characters.editCharacter")}
        </h2>
        <p className="mt-1 text-sm theme-text-muted">
          {t("characters.editCharacterSubtitle")}
        </p>
      </section>

      {/* Review notice for non-admin owners of public characters */}
      {review.submitForReview && (
        <div className="flex gap-3 rounded-xl border border-amber-400/40 bg-amber-400/10 p-4">
          <Info size={18} className="shrink-0 text-amber-400 mt-0.5" />
          <p className="text-sm text-amber-300">
            {review.kind === "draft"
              ? t("characters.draftSubmitNotice")
              : review.kind === "rejected"
                ? t("characters.rejectedResubmitNotice")
                : t("characters.reapprovalNotice")}
          </p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        <EditAvatarSection
          characterId={character.character_id}
          previewUrl={previewUrl}
          avatarFile={form.avatar}
          visualStyle={form.visual_style}
          isAdmin={isAdmin}
          onAvatarChange={(file) => updateField("avatar", file)}
          onPreviewChange={setPreviewUrl}
          onError={setSubmitError}
          onImageGenerated={handleImageGenerated}
        />

        <EditBasicInfoSection
          form={form}
          updateField={updateField}
          errors={errors}
        />

        <EditWizardFieldsSection
          form={form}
          updateField={updateField}
          occupations={occupations}
          hobbiesList={hobbiesList}
        />

        <EditPersonalitySection
          form={form}
          updateField={updateField}
          errors={errors}
        />

        <EditGallerySection
          characterId={character.character_id}
          images={galleryImages}
          visualStyle={form.visual_style}
          isAdmin={isAdmin}
          showApprovalNote={!isAdmin && Boolean(form.is_public)}
          onRefresh={async () => {
            const res = await listGalleryImages(character.character_id);
            setGalleryImages(res?.data?.images || []);
          }}
          onError={setSubmitError}
          onImageGenerated={handleImageGenerated}
        />

        {/* Admin-only controls: status, active toggle, featured, nsfw */}
        {isAdmin && (
          <EditAdminControlsSection
            form={form}
            updateField={updateField}
            errors={errors}
          />
        )}

        {submitError && (
          <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3">
            <p className="text-sm text-rose-300">{submitError}</p>
          </div>
        )}

        {/* Missing fields feedback */}
        {!canSubmit && missingFields.length > 0 && (
          <div className="rounded-xl border border-amber-400/40 bg-amber-400/10 p-3">
            <p className="text-sm text-amber-300">
              {t("characters.pleaseFillIn")}: {missingFields.join(", ")}
            </p>
          </div>
        )}

        <div className="flex gap-3">
          <PrimaryButton
            type="button"
            variant="secondary"
            fullWidth
            onClick={() => navigate(-1)}
          >
            {t("common.cancel")}
          </PrimaryButton>
          <PrimaryButton
            type="submit"
            fullWidth
            disabled={isSubmitting || !canSubmit}
          >
            {isSubmitting && (
              <Loader2 className="animate-spin mr-2" size={16} />
            )}
            {isSubmitting
              ? t("characters.saving")
              : review.submitForReview
                ? t("characters.saveAndSubmitForReview")
                : t("characters.saveChanges")}
          </PrimaryButton>
        </div>
      </form>

      {/* Prompt Display Modal */}
      {showPromptModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-2xl rounded-2xl p-6 shadow-lg theme-card">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-lg font-semibold theme-text">
                {t("characters.promptUsed")}
              </h3>
              <button
                onClick={() => setShowPromptModal(false)}
                className="rounded-full p-1 text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
              >
                <X size={20} />
              </button>
            </div>
            <div
              className="rounded-xl border p-4 text-sm theme-text whitespace-pre-wrap break-words max-h-96 overflow-y-auto"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-input)",
              }}
            >
              {promptUsed}
            </div>
            <div className="mt-4 flex justify-end">
              <button
                onClick={() => setShowPromptModal(false)}
                className="rounded-xl px-4 py-2 text-sm font-medium bg-[var(--color-accent)] text-[#082f49]"
              >
                {t("common.close")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
