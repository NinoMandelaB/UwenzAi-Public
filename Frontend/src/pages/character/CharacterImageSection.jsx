import { useRef } from "react";
import { ImagePlus, X } from "lucide-react";
import PrimaryButton from "../../components/PrimaryButton";
import { FieldError } from "./CharacterFormFields";

export default function CharacterImageSection({
  previewUrl,
  avatar,
  error,
  onAvatarChange,
  onClearAvatar,
  disabled = false,
}) {
  const fileInputRef = useRef(null);

  const handleChooseClick = () => {
    if (disabled) return;
    fileInputRef.current?.click();
  };

  const handleClear = () => {
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }

    onClearAvatar?.();
  };

  return (
    <section>
      <div className="rounded-2xl border p-4 theme-card">
        <div className="space-y-4">
          <div
            className="flex min-h-[220px] items-center justify-center overflow-hidden rounded-2xl border border-dashed"
            style={{
              borderColor: error
                ? "rgba(244, 63, 94, 0.6)"
                : "var(--color-border)",
              backgroundColor: "var(--color-soft)",
            }}
          >
            {previewUrl ? (
              <div className="relative h-full w-full">
                <img
                  src={previewUrl}
                  alt="Character preview"
                  className="h-[220px] w-full rounded-2xl object-cover"
                />

                <button
                  type="button"
                  onClick={handleClear}
                  disabled={disabled}
                  className="absolute right-3 top-3 inline-flex h-9 w-9 items-center justify-center rounded-full bg-black/60 text-white transition hover:bg-black/75 disabled:cursor-not-allowed disabled:opacity-60"
                  aria-label="Remove image"
                >
                  <X size={16} />
                </button>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
                <div
                  className="mb-4 flex h-14 w-14 items-center justify-center rounded-full"
                  style={{
                    backgroundColor: "var(--color-accent-soft)",
                    color: "var(--color-accent)",
                  }}
                >
                  <ImagePlus size={24} />
                </div>

                <p className="text-sm font-medium theme-text">
                  Upload character image
                </p>
                <p className="mt-2 max-w-md text-sm leading-6 theme-text-muted">
                  Choose an image, then crop and resize it before submission.
                </p>
              </div>
            )}
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            onChange={onAvatarChange}
            className="hidden"
            disabled={disabled}
          />

          <div className="flex flex-col gap-3 sm:flex-row">
            <button
              type="button"
              onClick={handleChooseClick}
              disabled={disabled}
              className="inline-flex w-full items-center justify-center rounded-xl px-4 py-3 text-sm font-medium transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60 sm:flex-1"
              style={{
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text)",
                border: "1px solid var(--color-border)",
              }}
            >
              {avatar ? "Change Image" : "Choose Image"}
            </button>

            {avatar && (
              <PrimaryButton
                type="button"
                variant="secondary"
                fullWidth
                onClick={handleClear}
                disabled={disabled}
              >
                Remove Image
              </PrimaryButton>
            )}
          </div>

          <FieldError message={error} />
        </div>
      </div>
    </section>
  );
}
