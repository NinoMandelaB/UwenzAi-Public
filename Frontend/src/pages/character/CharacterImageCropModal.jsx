import { useCallback, useEffect, useState } from "react";
import Cropper from "react-easy-crop";
import { Loader2, X } from "lucide-react";

import PrimaryButton from "../../components/PrimaryButton";
import { blobToFile, getCroppedImageBlob } from "./imageCropUtils";

export default function CharacterImageCropModal({
  isOpen,
  imageSrc,
  onClose,
  onConfirm,
}) {
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [croppedAreaPixels, setCroppedAreaPixels] = useState(null);
  const [isSaving, setIsSaving] = useState(false);
  const [cropError, setCropError] = useState("");

  useEffect(() => {
    if (!isOpen) {
      setCrop({ x: 0, y: 0 });
      setZoom(1);
      setCroppedAreaPixels(null);
      setCropError("");
      setIsSaving(false);
    }
  }, [isOpen]);

  const handleCropComplete = useCallback((_, croppedPixels) => {
    setCroppedAreaPixels(croppedPixels);
  }, []);

  const handleConfirm = async () => {
    if (!imageSrc || !croppedAreaPixels) {
      setCropError("Please adjust the image before continuing.");
      return;
    }

    try {
      setIsSaving(true);
      setCropError("");

      const blob = await getCroppedImageBlob(imageSrc, croppedAreaPixels, {
        outputWidth: 800,
        outputHeight: 800,
        mimeType: "image/jpeg",
        quality: 0.9,
      });

      const file = blobToFile(blob, "character-avatar.jpg");
      onConfirm(file);
    } catch (error) {
      setCropError(error?.message || "Failed to crop image.");
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 px-4 py-4 sm:py-6">
      <div className="flex max-h-full w-full max-w-2xl flex-col rounded-3xl border p-4 shadow-2xl theme-card overflow-hidden">
        {/* Header - Shrink-wrapped */}
        <div className="mb-4 flex items-start justify-between gap-4 shrink-0">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold theme-text truncate">
              Crop Image
            </h2>
            <p className="mt-0.5 text-xs sm:text-sm theme-text-muted">
              Adjust your character's avatar.
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition hover:opacity-90"
            style={{ borderColor: "var(--color-border)" }}
            disabled={isSaving}
          >
            <X size={18} />
          </button>
        </div>

        {/* Content Area - Scrollable if needed, but flex-grow keeps cropper large */}
        <div className="flex flex-1 flex-col min-h-0 space-y-4">
          {/* Cropper Container - flex-1 allows it to take remaining space */}
          <div
            className="relative flex-1 min-h-[250px] w-full overflow-hidden rounded-2xl sm:rounded-3xl"
            style={{
              backgroundColor: "#111827",
            }}
          >
            <Cropper
              image={imageSrc}
              crop={crop}
              zoom={zoom}
              aspect={1}
              cropShape="rect"
              showGrid={false}
              objectFit="contain"
              onCropChange={setCrop}
              onZoomChange={setZoom}
              onCropComplete={handleCropComplete}
            />
          </div>

          {/* Controls Area - Shrink-wrapped */}
          <div className="space-y-4 shrink-0">
            <div className="space-y-2">
              <div className="flex justify-between items-center">
                <label className="text-xs font-medium theme-text">Zoom</label>
                <span className="text-[10px] theme-text-muted">
                  {Math.round(zoom * 100)}%
                </span>
              </div>
              <input
                type="range"
                min={1}
                max={3}
                step={0.01}
                value={zoom}
                onChange={(event) => setZoom(Number(event.target.value))}
                className="w-full h-1.5 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                disabled={isSaving}
              />
            </div>

            {cropError && (
              <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-2.5">
                <p className="text-xs text-rose-300">{cropError}</p>
              </div>
            )}

            <div className="flex flex-col gap-2 sm:flex-row pt-1">
              <div className="flex-1 order-2 sm:order-1">
                <PrimaryButton
                  type="button"
                  variant="secondary"
                  fullWidth
                  onClick={onClose}
                  disabled={isSaving}
                >
                  Cancel
                </PrimaryButton>
              </div>

              <div className="flex-[1.5] order-1 sm:order-2">
                <PrimaryButton
                  type="button"
                  fullWidth
                  onClick={handleConfirm}
                  disabled={isSaving}
                >
                  <span className="inline-flex items-center justify-center gap-2">
                    {isSaving && <Loader2 size={16} className="animate-spin" />}
                    {isSaving ? "Saving..." : "Use This Image"}
                  </span>
                </PrimaryButton>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
