import { X, ChevronLeft, ChevronRight } from "lucide-react";

export default function CharacterLightbox({
  images,
  index,
  onClose,
  onPrev,
  onNext,
}) {
  if (index === null) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center">
      <button
        onClick={onClose}
        className="absolute top-4 right-4 text-white p-2 rounded-full bg-black/50 hover:bg-black/70 z-10"
      >
        <X size={24} />
      </button>

      {images.length > 1 && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onPrev();
          }}
          className="absolute left-4 text-white p-3 rounded-full bg-black/50 hover:bg-black/70"
        >
          <ChevronLeft size={28} />
        </button>
      )}

      <img
        src={images[index]}
        alt=""
        className="max-h-[90vh] max-w-[90vw] object-contain rounded-xl"
      />

      {images.length > 1 && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onNext();
          }}
          className="absolute right-4 text-white p-3 rounded-full bg-black/50 hover:bg-black/70"
        >
          <ChevronRight size={28} />
        </button>
      )}

      <p className="absolute bottom-4 text-white/70 text-sm">
        {index + 1} / {images.length}
      </p>
    </div>
  );
}
