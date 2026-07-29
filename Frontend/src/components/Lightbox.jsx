import { useEffect } from "react";
import { X, ChevronLeft, ChevronRight } from "lucide-react";

export default function Lightbox({
  images,
  currentIndex,
  onClose,
  onNext,
  onPrev,
}) {
  const currentImage = images[currentIndex];

  useEffect(() => {
    if (!currentImage) return;

    const handleKeyDown = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") onPrev();
      if (e.key === "ArrowRight") onNext();
    };

    document.addEventListener("keydown", handleKeyDown);
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [currentImage, onClose, onNext, onPrev]);

  if (!currentImage) return null;

  return (
    <div
      className="fixed inset-0 z-[130] flex items-center justify-center bg-black/90 backdrop-blur-sm"
      onClick={onClose}
    >
      {/* Close button */}
      <button
        type="button"
        onClick={onClose}
        className="absolute top-4 right-4 z-10 rounded-full bg-black/50 p-2 text-white backdrop-blur-md transition hover:bg-black/70"
      >
        <X size={24} />
      </button>

      {/* Navigation buttons */}
      {images.length > 1 && (
        <>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onPrev();
            }}
            className="absolute left-4 z-10 rounded-full bg-black/50 p-3 text-white backdrop-blur-md transition hover:bg-black/70 disabled:opacity-30"
            disabled={currentIndex === 0}
          >
            <ChevronLeft size={28} />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onNext();
            }}
            className="absolute right-4 z-10 rounded-full bg-black/50 p-3 text-white backdrop-blur-md transition hover:bg-black/70 disabled:opacity-30"
            disabled={currentIndex === images.length - 1}
          >
            <ChevronRight size={28} />
          </button>
        </>
      )}

      {/* Image container */}
      <div
        className="max-h-[90vh] max-w-[90vw]"
        onClick={(e) => e.stopPropagation()}
      >
        <img
          src={currentImage.image_url}
          alt={currentImage.caption || "Gallery image"}
          className="max-h-[85vh] max-w-full object-contain rounded-lg shadow-2xl"
        />
        {currentImage.caption && (
          <p className="mt-3 text-center text-sm text-white/80">
            {currentImage.caption}
          </p>
        )}
        <p className="mt-1 text-center text-xs text-white/50">
          {currentIndex + 1} / {images.length}
        </p>
      </div>
    </div>
  );
}
