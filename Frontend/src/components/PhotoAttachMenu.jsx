// src/components/PhotoAttachMenu.jsx
//
// The composer's photo button: opens a small menu with the two upload
// sources — "Choose photo" (gallery/file picker) and "Take photo" (the
// device camera via the capture attribute, mobile only; desktop browsers
// ignore it and open the file picker).
//
// The parent decides whether photos are available at all: render this only
// when the server allows photo sends (user.photo_uploads_enabled from
// auth/me), so the button is never a dead feature (audit F6).

import { useEffect, useRef, useState } from "react";
import { Camera, ImageIcon } from "lucide-react";
import { useLanguage } from "../context/LanguageContext";

const ACCEPTED_TYPES = "image/jpeg,image/png,image/webp";

export default function PhotoAttachMenu({ disabled = false, onFileSelected }) {
  const { t } = useLanguage();
  const [isOpen, setIsOpen] = useState(false);
  const rootRef = useRef(null);
  const galleryInputRef = useRef(null);
  const cameraInputRef = useRef(null);

  // Close on click-outside and Escape so the menu never lingers.
  useEffect(() => {
    if (!isOpen) return undefined;
    function handlePointerDown(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    function handleKeyDown(event) {
      if (event.key === "Escape") setIsOpen(false);
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  function selectSource(inputRef) {
    setIsOpen(false);
    inputRef.current?.click();
  }

  function handleInputChange(event, inputRef) {
    const file = event.target.files?.[0];
    if (file) onFileSelected?.(file);
    event.target.value = "";
    inputRef.current?.blur();
  }

  const menuButtonClass =
    "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition hover:opacity-80";

  return (
    <div ref={rootRef} className="relative">
      <input
        type="file"
        accept={ACCEPTED_TYPES}
        className="hidden"
        ref={galleryInputRef}
        onChange={(event) => handleInputChange(event, galleryInputRef)}
      />
      <input
        type="file"
        accept={ACCEPTED_TYPES}
        capture="environment"
        className="hidden"
        ref={cameraInputRef}
        onChange={(event) => handleInputChange(event, cameraInputRef)}
      />
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        title={t("chat.attachPhoto")}
        aria-label={t("chat.attachPhoto")}
        className="flex h-11 w-11 items-center justify-center rounded-2xl border transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-soft)",
          color: "var(--color-text-muted)",
        }}
      >
        <Camera size={18} />
      </button>

      {isOpen && (
        <div
          role="menu"
          aria-label={t("chat.attachPhoto")}
          className="absolute bottom-14 left-0 z-20 w-44 overflow-hidden rounded-xl border shadow-lg"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-card)",
          }}
        >
          <button
            type="button"
            role="menuitem"
            className={menuButtonClass}
            style={{ color: "var(--color-text)" }}
            onClick={() => selectSource(galleryInputRef)}
          >
            <ImageIcon size={16} />
            {t("chat.choosePhoto")}
          </button>
          <button
            type="button"
            role="menuitem"
            className={menuButtonClass}
            style={{ color: "var(--color-text)" }}
            onClick={() => selectSource(cameraInputRef)}
          >
            <Camera size={16} />
            {t("chat.takePhoto")}
          </button>
        </div>
      )}
    </div>
  );
}
