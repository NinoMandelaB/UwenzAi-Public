// src/pages/edit/EditAvatarSection.jsx

import { useState, useRef } from "react";
import { clearOperationId, reuseOperationId } from "../../utils/idempotency";
import { Loader2, Sparkles, Upload, Trash2 } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";
import { generateAvatar } from "../../services/characterService";
import { getCharacterMessage } from "../../utils/characterMessageMapper";
import CharacterImageCropModal from "../character/CharacterImageCropModal";
import PrimaryButton from "../../components/PrimaryButton";
import {
  validateAvatarFile,
  validateAvatarDimensions,
} from "../character/characterFormValidation";

export default function EditAvatarSection({
  characterId,
  previewUrl,
  avatarFile,
  visualStyle,
  isAdmin = false,
  onAvatarChange,
  onPreviewChange,
  onError,
  onImageGenerated,
}) {
  const { t } = useLanguage();
  const [isGenerating, setIsGenerating] = useState(false);

  // One key per generation attempt, reused if that attempt is retried, so a
  // request the server already charged for is never paid for twice.
  const avatarOperationRef = useRef(null);
  const [cropModalOpen, setCropModalOpen] = useState(false);
  const [rawImageUrl, setRawImageUrl] = useState("");
  const requestIdRef = useRef(0);

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
      const result = await generateAvatar(characterId, {
        prompt:
          "bright natural lighting, relaxed expression, clean fresh skin, professional headshot",
        style_preset: visualStyle === "anime" ? "anime" : "photographic",
        extra_prompt: extra,
        operationId: reuseOperationId(avatarOperationRef, extra),
      });
      clearOperationId(avatarOperationRef);

      if (result?.data?.prompt_used) {
        onImageGenerated?.(result.data.prompt_used);
      }

      window.location.reload();
    } catch (error) {
      onError(getCharacterMessage(error, t("characters.avatarGenerateFailed")));
    } finally {
      setIsGenerating(false);
    }
  };

  const confirmExtraPrompt = () => {
    setShowExtraPrompt(false);
    handleGenerate(extraPromptText);
  };

  // ----- File upload + crop -----
  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const requestId = ++requestIdRef.current;
    const error = validateAvatarFile(file);
    if (error) {
      if (requestId !== requestIdRef.current) return;
      onError(error);
      return;
    }
    const dimensionError = await validateAvatarDimensions(file);
    if (requestId !== requestIdRef.current) return;
    if (dimensionError) {
      onError(dimensionError);
      return;
    }
    if (rawImageUrl) URL.revokeObjectURL(rawImageUrl);
    const objectUrl = URL.createObjectURL(file);
    setRawImageUrl(objectUrl);
    setCropModalOpen(true);
  };

  const handleCropConfirm = (croppedFile) => {
    onAvatarChange(croppedFile);
    onPreviewChange(URL.createObjectURL(croppedFile));
    setCropModalOpen(false);
    URL.revokeObjectURL(rawImageUrl);
    setRawImageUrl("");
  };

  // Determine preview source
  const imageSrc = previewUrl
    ? previewUrl
    : avatarFile
      ? URL.createObjectURL(avatarFile)
      : null;

  return (
    <div className="rounded-2xl border p-5 theme-card space-y-4">
      {/* ── Portrait preview (centered, not stretched) ── */}
      <div className="flex justify-center">
        <div className="w-40 aspect-[3/4] rounded-2xl overflow-hidden border border-[var(--color-border)] bg-[var(--color-soft)]">
          {imageSrc ? (
            <img
              src={imageSrc}
              alt={t("characters.avatar")}
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-xs text-[var(--color-text-muted)]">
              {t("characters.noImage")}
            </div>
          )}
        </div>
      </div>

      {/* ── Action buttons ── */}
      <div className="flex flex-wrap items-center justify-center gap-3">
        {/* Generate AI button */}
        <button
          type="button"
          onClick={openExtraPrompt}
          disabled={isGenerating}
          className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium bg-[var(--color-accent-soft)] text-[var(--color-accent)] border border-[var(--color-accent-soft)] transition hover:opacity-90"
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
          <label className="flex cursor-pointer items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium bg-[var(--color-soft)] text-[var(--color-text)] border border-[var(--color-border)] transition hover:opacity-90">
            <Upload size={16} />
            {t("characters.upload")}
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={handleFileChange}
              className="hidden"
            />
          </label>
        )}
      </div>

      {/* ── Crop Modal ── */}
      <CharacterImageCropModal
        isOpen={cropModalOpen}
        imageSrc={rawImageUrl}
        onClose={() => {
          setCropModalOpen(false);
          URL.revokeObjectURL(rawImageUrl);
          setRawImageUrl("");
        }}
        onConfirm={handleCropConfirm}
      />

      {/* ── Extra Prompt Modal ── */}
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
    </div>
  );
}
