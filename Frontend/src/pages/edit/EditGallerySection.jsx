// src/pages/edit/EditGallerySection.jsx

import { useRef, useState } from "react";
import { clearOperationId, reuseOperationId } from "../../utils/idempotency";
import { Loader2, Sparkles, Plus, Trash2 } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";
import {
  generateGalleryImage,
  uploadGalleryImage,
  deleteGalleryImage,
} from "../../services/characterService";
import { getCharacterMessage } from "../../utils/characterMessageMapper";
import ConfirmDialog from "../../components/ConfirmDialog";
import PrimaryButton from "../../components/PrimaryButton";

export default function EditGallerySection({
  characterId,
  images,
  visualStyle,
  isAdmin = false,
  showApprovalNote = false,
  onRefresh,
  onError,
  onImageGenerated,
}) {
  const { t } = useLanguage();
  const [isGenerating, setIsGenerating] = useState(false);

  // One key per generation attempt, reused if that attempt is retried, so a
  // request the server already charged for is never paid for twice.
  const galleryOperationRef = useRef(null);
  const [isUploading, setIsUploading] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [imageToDelete, setImageToDelete] = useState(null);
  const [isConfirmOpen, setIsConfirmOpen] = useState(false);
  const fileInputRef = useRef(null);

  // ----- Extra prompt modal state -----
  const [showExtraPrompt, setShowExtraPrompt] = useState(false);
  const [extraPromptText, setExtraPromptText] = useState("");

  const openExtraPrompt = () => {
    setExtraPromptText("");
    setShowExtraPrompt(true);
  };

  const handleGenerate = async (extra = "") => {
    if (!characterId || isGenerating) return;
    setIsGenerating(true);
    try {
      const result = await generateGalleryImage(characterId, {
        prompt:
          "bright natural lighting, relaxed full-body pose, clean fresh skin, professional fashion photography",
        style_preset: visualStyle === "anime" ? "anime" : "photographic",
        extra_prompt: extra,
        // Part of the character's own gallery: goes public with its approval.
        publish_on_approval: true,
        operationId: reuseOperationId(galleryOperationRef, extra),
      });
      clearOperationId(galleryOperationRef);

      if (result?.data?.prompt_used) {
        onImageGenerated?.(result.data.prompt_used);
      }

      await onRefresh();
    } catch (error) {
      onError(
        getCharacterMessage(error, t("characters.galleryGenerateFailed")),
      );
    } finally {
      setIsGenerating(false);
    }
  };

  const confirmExtraPrompt = () => {
    setShowExtraPrompt(false);
    handleGenerate(extraPromptText);
  };

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file || !characterId) return;
    setIsUploading(true);
    try {
      await uploadGalleryImage(characterId, file);
      await onRefresh();
    } catch (error) {
      onError(getCharacterMessage(error, t("characters.galleryUploadFailed")));
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDeleteClick = (image) => {
    setImageToDelete(image);
    setIsConfirmOpen(true);
  };

  const handleConfirmDelete = async () => {
    if (!imageToDelete) return;
    setDeletingId(imageToDelete.id);
    setIsConfirmOpen(false);
    try {
      await deleteGalleryImage(imageToDelete.id);
      await onRefresh();
      onError(null);
    } catch (error) {
      onError(getCharacterMessage(error, t("characters.galleryDeleteFailed")));
    } finally {
      setDeletingId(null);
      setImageToDelete(null);
    }
  };

  const handleCancelDelete = () => {
    setIsConfirmOpen(false);
    setImageToDelete(null);
  };

  return (
    <>
      <div className="rounded-2xl border p-4 theme-card space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold theme-text">
            {t("characters.gallery")}
          </h3>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={openExtraPrompt}
              disabled={isGenerating}
              className="flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium bg-[var(--color-accent-soft)] text-[var(--color-accent)] disabled:opacity-50"
            >
              {isGenerating ? (
                <Loader2 className="animate-spin" size={16} />
              ) : (
                <Sparkles size={16} />
              )}
              {t("characters.generate")}
            </button>
            {/* Upload button — admins only, mirroring the server-side restriction */}
            {isAdmin && (
              <label className="flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium bg-[var(--color-soft)] text-[var(--color-text)] border border-[var(--color-border)] cursor-pointer">
                {isUploading ? (
                  <Loader2 className="animate-spin" size={16} />
                ) : (
                  <Plus size={16} />
                )}
                {t("characters.addImage")}
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  ref={fileInputRef}
                  onChange={handleFileChange}
                  className="hidden"
                  disabled={isUploading}
                />
              </label>
            )}
          </div>
        </div>

        {showApprovalNote && (
          <p className="text-xs theme-text-muted">
            {t("characters.galleryPublishNote")}
          </p>
        )}

        {images.length === 0 ? (
          <p className="text-sm theme-text-muted text-center py-4">
            {t("characters.noGalleryImages")}
          </p>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            {images.map((img) => (
              <div
                key={img.id}
                className="relative aspect-square rounded-xl overflow-hidden border border-white/10 group"
              >
                <img
                  src={img.image_url}
                  alt={img.caption || t("characters.galleryImage")}
                  className="h-full w-full object-cover"
                />
                {img.publish_on_approval && (
                  <span className="absolute bottom-1 left-1 right-1 rounded-md bg-black/60 px-1.5 py-0.5 text-center text-[10px] leading-tight text-white backdrop-blur-sm">
                    {t("characters.galleryPublishOnApproval")}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => handleDeleteClick(img)}
                  disabled={deletingId === img.id}
                  className="absolute top-1 right-1 rounded-full p-1.5 text-white bg-black/60 backdrop-blur-sm transition hover:bg-red-600 disabled:opacity-50"
                  style={{ boxShadow: "0 2px 8px rgba(0,0,0,0.3)" }}
                >
                  {deletingId === img.id ? (
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

      {showExtraPrompt && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-2xl p-6 shadow-lg theme-card">
            <h3 className="text-lg font-semibold theme-text mb-2">
              {t("characters.addExtraDetails")}
            </h3>
            <p className="text-sm theme-text-muted mb-4">
              {t("characters.extraDetailsHint")}
            </p>
            <textarea
              autoFocus
              rows={3}
              value={extraPromptText}
              onChange={(e) => setExtraPromptText(e.target.value)}
              placeholder={t("characters.extraDetailsPlaceholder")}
              className="w-full rounded-xl border px-4 py-3 text-sm theme-text theme-input"
            />
            <div className="flex justify-end gap-3 mt-4">
              <button
                onClick={() => setShowExtraPrompt(false)}
                className="rounded-xl px-4 py-2 text-sm font-medium theme-text-muted hover:theme-soft"
              >
                {t("common.cancel")}
              </button>
              <PrimaryButton onClick={confirmExtraPrompt}>
                {t("characters.generate")}
              </PrimaryButton>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        isOpen={isConfirmOpen}
        title={t("characters.deleteGalleryImage")}
        message={t("characters.deleteGalleryImageConfirm")}
        confirmText={t("common.delete")}
        isLoading={deletingId !== null}
        onConfirm={handleConfirmDelete}
        onClose={handleCancelDelete}
      />
    </>
  );
}
