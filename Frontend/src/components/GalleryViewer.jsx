import { useState, useEffect } from "react";
import { listGalleryImages } from "../services/characterService";
import { Loader2, ImageOff } from "lucide-react";
import Lightbox from "./Lightbox";

export default function GalleryViewer({ characterId }) {
  const [images, setImages] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [currentImageIndex, setCurrentImageIndex] = useState(0);

  useEffect(() => {
    if (!characterId) return;

    async function fetchGallery() {
      setIsLoading(true);
      setError(null);
      try {
        const response = await listGalleryImages(characterId);
        setImages(response?.data?.images || []);
      } catch (err) {
        console.error("Failed to load gallery:", err);
        setError("Could not load gallery images.");
      } finally {
        setIsLoading(false);
      }
    }

    fetchGallery();
  }, [characterId]);

  const openLightbox = (index) => {
    setCurrentImageIndex(index);
    setLightboxOpen(true);
  };

  const closeLightbox = () => {
    setLightboxOpen(false);
  };

  const nextImage = () => {
    setCurrentImageIndex((prev) => Math.min(prev + 1, images.length - 1));
  };

  const prevImage = () => {
    setCurrentImageIndex((prev) => Math.max(prev - 1, 0));
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 className="animate-spin theme-text-muted" size={20} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm theme-text-muted">
        <ImageOff size={18} />
        <span>{error}</span>
      </div>
    );
  }

  if (images.length === 0) {
    return (
      <p className="py-4 text-sm theme-text-muted text-center">
        No gallery images yet.
      </p>
    );
  }

  return (
    <>
      <div className="flex gap-3 overflow-x-auto pb-2 snap-x snap-mandatory">
        {images.map((img, index) => (
          <button
            key={img.id}
            type="button"
            onClick={() => openLightbox(index)}
            className="shrink-0 w-28 h-28 rounded-xl overflow-hidden border border-white/10 snap-start cursor-pointer hover:opacity-90 transition"
          >
            <img
              src={img.image_url}
              alt={img.caption || "Gallery image"}
              className="w-full h-full object-cover"
            />
          </button>
        ))}
      </div>

      {lightboxOpen && (
        <Lightbox
          images={images}
          currentIndex={currentImageIndex}
          onClose={closeLightbox}
          onNext={nextImage}
          onPrev={prevImage}
        />
      )}
    </>
  );
}
