// src/pages/CharacterImageSetup.jsx

import { useState, useEffect, useRef } from "react";
import { clearOperationId, reuseOperationId } from "../utils/idempotency";
import { useParams, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import {
  getCharacterById,
  generateAvatar,
  updateCharacter,
  listGalleryImages,
  generateGalleryImage,
  uploadGalleryImage,
  deleteGalleryImage,
  completeCharacterSetup,
} from "../services/characterService";
import { getPricing } from "../services/billingService";
import { getCharacterMessage } from "../utils/characterMessageMapper";
import PageCard from "../components/PageCard";
import PrimaryButton from "../components/PrimaryButton";
import MessagePopup from "../components/MessagePopup";
import {
  Loader2,
  Upload,
  Sparkles,
  Trash2,
  CheckCircle,
  Image as ImageIcon,
  Plus,
  X,
} from "lucide-react";

export default function CharacterImageSetup() {
  const { characterId } = useParams();
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { user, isAdmin } = useAuth();

  const [character, setCharacter] = useState(null);
  const [galleryImages, setGalleryImages] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  // Avatar state
  const [isGeneratingAvatar, setIsGeneratingAvatar] = useState(false);
  const [isAutoGenerating, setIsAutoGenerating] = useState(false);
  // Guards the one automatic first-avatar attempt per mount so React's
  // double-invoked effects (StrictMode) or re-fetches cannot fire it twice.
  const autoGenerationAttemptedRef = useRef(false);

  // One key per generation attempt, reused if that attempt is retried, so a
  // request the server already charged for is never paid for twice.
  const avatarOperationRef = useRef(null);
  const galleryOperationRef = useRef(null);
  const [isUploadingAvatar, setIsUploadingAvatar] = useState(false);
  const avatarFileInputRef = useRef(null);

  // Spark cost of a paid (regeneration) avatar, from the pricing endpoint.
  const [avatarRegenerationCost, setAvatarRegenerationCost] = useState(null);

  // Gallery state
  const [isGeneratingGallery, setIsGeneratingGallery] = useState(false);
  const [isUploadingGallery, setIsUploadingGallery] = useState(false);
  const [deletingImageId, setDeletingImageId] = useState(null);
  const galleryFileInputRef = useRef(null);

  // Complete setup
  const [isCompleting, setIsCompleting] = useState(false);
  const [showSuccessPopup, setShowSuccessPopup] = useState(false);
  const [successMessage, setSuccessMessage] = useState("");

  // ---------- Prompt display modal state ----------
  const [showPromptModal, setShowPromptModal] = useState(false);
  const [promptUsed, setPromptUsed] = useState("");

  // ---------- Extra prompt input modal state ----------
  const [showExtraPrompt, setShowExtraPrompt] = useState(false);
  const [extraPromptText, setExtraPromptText] = useState("");
  const [extraPromptTarget, setExtraPromptTarget] = useState("avatar"); // "avatar" or "gallery"

  // Fetch character and gallery on mount
  useEffect(() => {
    let cancelled = false;
    async function fetchData() {
      setIsLoading(true);
      setError(null);
      try {
        const [charRes, galleryRes] = await Promise.all([
          getCharacterById(characterId),
          listGalleryImages(characterId),
        ]);
        if (cancelled) return;
        const fetched = charRes?.data?.character;
        setCharacter(fetched);
        setGalleryImages(galleryRes?.data?.images || []);

        // ── Automatic first avatar ──────────────────────────────
        // The wizard drops the user here with a DRAFT character and no
        // avatar; without one the character never reaches the gallery.
        // The first avatar is free server-side (included in the character
        // creation fee), so kick it off right away. The user can still
        // regenerate afterwards — those cost sparks, billed by the backend.
        if (
          fetched &&
          !fetched.avatar_url &&
          // Serialized statuses are the lowercase model values ("draft", …).
          fetched.review_status === "draft" &&
          !autoGenerationAttemptedRef.current
        ) {
          autoGenerationAttemptedRef.current = true;
          handleGenerateAvatar("", { isAuto: true });
        }
      } catch (err) {
        if (!cancelled) {
          setError(getCharacterMessage(err, "Failed to load character."));
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    fetchData();
    return () => {
      cancelled = true;
    };
  }, [characterId]);

  // Fetch the paid-avatar cost once, to show the regeneration hint
  useEffect(() => {
    let cancelled = false;
    getPricing()
      .then((res) => {
        if (!cancelled && res?.data?.image_generation_cost != null) {
          setAvatarRegenerationCost(res.data.image_generation_cost);
        }
      })
      .catch(() => {
        // Non-critical: the hint is simply omitted when pricing is unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Refresh character avatar after update
  const refreshCharacter = async () => {
    try {
      const res = await getCharacterById(characterId);
      setCharacter(res?.data?.character);
    } catch (err) {
      console.error("Failed to refresh character:", err);
    }
  };

  // Refresh gallery images
  const refreshGallery = async () => {
    try {
      const res = await listGalleryImages(characterId);
      setGalleryImages(res?.data?.images || []);
    } catch (err) {
      console.error("Failed to refresh gallery:", err);
    }
  };

  // Open the extra prompt modal for a specific target
  const openExtraPrompt = (target) => {
    setExtraPromptTarget(target);
    setExtraPromptText("");
    setShowExtraPrompt(true);
  };

  // Perform the actual generation after the user confirms extra prompt
  const handleConfirmExtraPrompt = async () => {
    setShowExtraPrompt(false);
    if (extraPromptTarget === "avatar") {
      await handleGenerateAvatar(extraPromptText);
    } else {
      await handleGenerateGalleryImage(extraPromptText);
    }
  };

  // Handle avatar generation (AI) – with optional extra prompt.
  // `isAuto` marks the automatic first-avatar attempt triggered on arrival.
  const handleGenerateAvatar = async (extra = "", { isAuto = false } = {}) => {
    if (isGeneratingAvatar || isAutoGenerating) return;
    if (isAuto) setIsAutoGenerating(true);
    else setIsGeneratingAvatar(true);
    setError(null);
    try {
      const result = await generateAvatar(characterId, {
        prompt: "portrait, high quality, detailed",
        style_preset:
          character?.visual_style === "anime" ? "anime" : "photographic",
        extra_prompt: extra,
        operationId: reuseOperationId(avatarOperationRef, extra),
      });
      clearOperationId(avatarOperationRef);
      await refreshCharacter();
      // If the response contains the prompt used, show it — but not for the
      // automatic attempt, where an unprompted modal is just noise.
      if (!isAuto && result?.data?.prompt_used) {
        setPromptUsed(result.data.prompt_used);
        setShowPromptModal(true);
      }
    } catch (err) {
      setError(
        getCharacterMessage(
          err,
          isAuto
            ? t("imageSetup.autoGenerateFailed")
            : t("characters.avatarGenerateFailed"),
        ),
      );
    } finally {
      if (isAuto) setIsAutoGenerating(false);
      else setIsGeneratingAvatar(false);
    }
  };

  // Handle avatar upload (admin only)
  const handleAvatarFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploadingAvatar(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("avatar", file);
      await updateCharacter(characterId, { avatar: file });
      await refreshCharacter();
    } catch (err) {
      setError(getCharacterMessage(err, "Failed to upload avatar."));
    } finally {
      setIsUploadingAvatar(false);
    }
  };

  // Handle gallery AI generation – with optional extra prompt
  const handleGenerateGalleryImage = async (extra = "") => {
    if (isGeneratingGallery) return;
    setIsGeneratingGallery(true);
    setError(null);
    try {
      const result = await generateGalleryImage(characterId, {
        prompt: "full body, stylish pose, high quality",
        style_preset:
          character?.visual_style === "anime" ? "anime" : "photographic",
        extra_prompt: extra,
        // Part of the character's own gallery: goes public with its approval.
        publish_on_approval: true,
        operationId: reuseOperationId(galleryOperationRef, extra),
      });
      clearOperationId(galleryOperationRef);
      await refreshGallery();
      if (result?.data?.prompt_used) {
        setPromptUsed(result.data.prompt_used);
        setShowPromptModal(true);
      }
    } catch (err) {
      setError(getCharacterMessage(err, t("characters.galleryGenerateFailed")));
    } finally {
      setIsGeneratingGallery(false);
    }
  };

  // Handle gallery upload (admin only)
  const handleGalleryFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploadingGallery(true);
    setError(null);
    try {
      await uploadGalleryImage(characterId, file);
      await refreshGallery();
    } catch (err) {
      setError(getCharacterMessage(err, t("characters.galleryUploadFailed")));
    } finally {
      setIsUploadingGallery(false);
      if (galleryFileInputRef.current) {
        galleryFileInputRef.current.value = "";
      }
    }
  };

  // Handle gallery image deletion
  const handleDeleteGalleryImage = async (imageId) => {
    if (deletingImageId === imageId) return;
    setDeletingImageId(imageId);
    setError(null);
    try {
      await deleteGalleryImage(imageId);
      await refreshGallery();
    } catch (err) {
      setError(getCharacterMessage(err, t("characters.galleryDeleteFailed")));
    } finally {
      setDeletingImageId(null);
    }
  };

  // Complete setup
  const handleCompleteSetup = async () => {
    if (!character?.avatar_url) {
      setError(t("imageSetup.avatarRequiredError"));
      return;
    }
    if (isCompleting) return;
    setIsCompleting(true);
    setError(null);
    try {
      await completeCharacterSetup(characterId, {
        avatar_source: character.avatar_source || "ai_generated",
      });
      setSuccessMessage(
        isAdmin
          ? t("imageSetup.adminSuccessMessage")
          : t("imageSetup.userSuccessMessage"),
      );
      setShowSuccessPopup(true);
      setTimeout(() => {
        navigate(`/characters/${characterId}`);
      }, 2000);
    } catch (err) {
      setError(getCharacterMessage(err, "Failed to complete setup."));
    } finally {
      setIsCompleting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="animate-spin text-cyan-400" size={32} />
      </div>
    );
  }

  if (!character) {
    return (
      <PageCard className="text-center">
        <p className="text-sm font-medium theme-text">
          {t("imageSetup.characterNotFound")}
        </p>
      </PageCard>
    );
  }

  const hasAvatar = Boolean(character.avatar_url);
  const avatarBusy = isGeneratingAvatar || isAutoGenerating;
  // A public character made by a non-admin only reaches the admins' review
  // queue once setup is completed, and its gallery goes live with the approval.
  const showApprovalNotes = !isAdmin && Boolean(character.is_public);

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Success Popup */}
      <MessagePopup
        open={showSuccessPopup}
        message={successMessage}
        type="success"
        title={t("imageSetup.setupCompleteTitle")}
        onClose={() => setShowSuccessPopup(false)}
      />

      {showExtraPrompt && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-2xl p-6 shadow-lg theme-card">
            <h3 className="text-lg font-semibold theme-text mb-2">
              {t("imageSetup.extraDetailsTitle")}
            </h3>
            <p className="text-sm theme-text-muted mb-4">
              {t("imageSetup.extraDetailsHint")}
            </p>
            <textarea
              autoFocus
              rows={3}
              value={extraPromptText}
              onChange={(e) => setExtraPromptText(e.target.value)}
              placeholder={t("imageSetup.extraDetailsPlaceholder")}
              className="w-full rounded-xl border px-4 py-3 text-sm theme-text theme-input"
            />
            <div className="flex justify-end gap-3 mt-4">
              <button
                onClick={() => setShowExtraPrompt(false)}
                className="rounded-xl px-4 py-2 text-sm font-medium theme-text-muted hover:theme-soft"
              >
                {t("imageSetup.cancel")}
              </button>
              <PrimaryButton onClick={handleConfirmExtraPrompt}>
                {t("chat.generate")}
              </PrimaryButton>
            </div>
          </div>
        </div>
      )}

      {showPromptModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-2xl rounded-2xl p-6 shadow-lg theme-card">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-lg font-semibold theme-text">
                {t("imageSetup.promptUsedTitle")}
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
                {t("imageSetup.close")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold theme-text">
          {t("imageSetup.title")}
        </h1>
        <p className="mt-1 text-sm theme-text-muted">
          {t("imageSetup.subtitle", { name: character.name })}
        </p>
      </div>

      {/* Error Display */}
      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Avatar Section */}
      <PageCard>
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold theme-text">
              {t("imageSetup.avatar")}
            </h2>
            <p className="text-sm theme-text-muted">
              {t("imageSetup.avatarRequired")}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => openExtraPrompt("avatar")}
              disabled={avatarBusy}
              className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
              style={{
                backgroundColor: "var(--color-accent-soft)",
                color: "var(--color-accent)",
              }}
            >
              {avatarBusy ? (
                <Loader2 className="animate-spin" size={16} />
              ) : (
                <Sparkles size={16} />
              )}
              {hasAvatar
                ? t("imageSetup.regenerateAvatar")
                : t("imageSetup.generateAvatar")}
            </button>
            {isAdmin && (
              <>
                <input
                  ref={avatarFileInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={handleAvatarFileChange}
                  className="hidden"
                />
                <button
                  type="button"
                  onClick={() => avatarFileInputRef.current?.click()}
                  disabled={isUploadingAvatar}
                  className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
                  style={{
                    backgroundColor: "var(--color-soft)",
                    color: "var(--color-text)",
                    border: "1px solid var(--color-border)",
                  }}
                >
                  {isUploadingAvatar ? (
                    <Loader2 className="animate-spin" size={16} />
                  ) : (
                    <Upload size={16} />
                  )}
                  {t("imageSetup.upload")}
                </button>
              </>
            )}
          </div>
        </div>

        <div className="mt-4">
          {/* Automatic first-avatar notice */}
          {isAutoGenerating && (
            <div className="mb-4 flex items-center justify-center gap-2 rounded-xl border border-cyan-400/30 bg-cyan-400/10 p-3 text-sm text-cyan-300">
              <Loader2 className="animate-spin" size={16} />
              {t("imageSetup.autoGenerating")}
            </div>
          )}
          {hasAvatar ? (
            <div className="relative mx-auto w-48 h-48 rounded-2xl overflow-hidden border-2 border-cyan-400/30">
              <img
                src={character.avatar_url}
                alt={t("imageSetup.avatar")}
                className="h-full w-full object-cover"
              />
            </div>
          ) : (
            <div
              className="flex h-48 w-48 items-center justify-center rounded-2xl border-2 border-dashed mx-auto"
              style={{ borderColor: "var(--color-border)" }}
            >
              <div className="text-center">
                <ImageIcon className="mx-auto h-10 w-10 theme-text-muted" />
                <p className="mt-2 text-sm theme-text-muted">
                  {t("imageSetup.noAvatarYet")}
                </p>
              </div>
            </div>
          )}
          {/* Cost hint: the first avatar is free, regenerating costs sparks */}
          <p className="mt-3 text-center text-xs theme-text-muted">
            {hasAvatar && avatarRegenerationCost != null
              ? t("imageSetup.regenerationCost", {
                  cost: avatarRegenerationCost,
                })
              : !hasAvatar && !avatarBusy
                ? t("imageSetup.firstAvatarFree")
                : ""}
          </p>
        </div>
      </PageCard>

      {/* Gallery Section */}
      <PageCard>
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-lg font-semibold theme-text">
              {t("imageSetup.gallery")}
            </h2>
            <p className="text-sm theme-text-muted">
              {t("imageSetup.galleryOptional")}
              {showApprovalNotes && ` ${t("imageSetup.galleryPublishNote")}`}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => openExtraPrompt("gallery")}
              disabled={isGeneratingGallery}
              className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
              style={{
                backgroundColor: "var(--color-accent-soft)",
                color: "var(--color-accent)",
              }}
            >
              {isGeneratingGallery ? (
                <Loader2 className="animate-spin" size={16} />
              ) : (
                <Sparkles size={16} />
              )}
              {t("chat.generate")}
            </button>
            {isAdmin && (
              <>
                <input
                  ref={galleryFileInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={handleGalleryFileChange}
                  className="hidden"
                />
                <button
                  type="button"
                  onClick={() => galleryFileInputRef.current?.click()}
                  disabled={isUploadingGallery}
                  className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
                  style={{
                    backgroundColor: "var(--color-soft)",
                    color: "var(--color-text)",
                    border: "1px solid var(--color-border)",
                  }}
                >
                  {isUploadingGallery ? (
                    <Loader2 className="animate-spin" size={16} />
                  ) : (
                    <Plus size={16} />
                  )}
                  {t("imageSetup.addImage")}
                </button>
              </>
            )}
          </div>
        </div>

        <div className="mt-4">
          {galleryImages.length === 0 ? (
            <div
              className="flex h-32 items-center justify-center rounded-xl border-2 border-dashed"
              style={{ borderColor: "var(--color-border)" }}
            >
              <p className="text-sm theme-text-muted">
                {t("imageSetup.noGalleryImages")}
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
              {galleryImages.map((img) => (
                <div
                  key={img.id}
                  className="group relative aspect-square rounded-xl overflow-hidden border border-white/10"
                >
                  <img
                    src={img.image_url}
                    alt={img.caption || "Gallery"}
                    className="h-full w-full object-cover"
                  />
                  {img.publish_on_approval && (
                    <span className="absolute bottom-1 left-1 right-1 rounded-md bg-black/60 px-1.5 py-0.5 text-center text-[10px] leading-tight text-white backdrop-blur-sm">
                      {t("characters.galleryPublishOnApproval")}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => handleDeleteGalleryImage(img.id)}
                    disabled={deletingImageId === img.id}
                    className="absolute top-2 right-2 rounded-full bg-black/60 p-1.5 text-white opacity-0 transition hover:bg-red-600 group-hover:opacity-100"
                  >
                    {deletingImageId === img.id ? (
                      <Loader2 className="animate-spin" size={14} />
                    ) : (
                      <Trash2 size={14} />
                    )}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </PageCard>

      {/* Bottom Actions */}
      <div
        className="flex items-center justify-end gap-4 border-t pt-6"
        style={{ borderColor: "var(--color-border)" }}
      >
        <button
          type="button"
          onClick={() => navigate(`/characters/${characterId}`)}
          className="text-sm font-medium theme-text-muted hover:underline"
        >
          {t("imageSetup.skipForNow")}
        </button>
        <PrimaryButton
          onClick={handleCompleteSetup}
          disabled={!hasAvatar || isCompleting || avatarBusy}
          loading={isCompleting}
        >
          {isCompleting
            ? t("imageSetup.completing")
            : t("imageSetup.completeSetup")}
        </PrimaryButton>
      </div>
      {showApprovalNotes && (
        <p className="text-right text-xs theme-text-muted">
          {t("imageSetup.draftHint")}
        </p>
      )}
    </div>
  );
}
