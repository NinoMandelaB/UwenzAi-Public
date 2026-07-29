// src/pages/details/CharacterGalleryCarousel.jsx

import { useEffect, useState } from "react";
import { Swiper, SwiperSlide } from "swiper/react";
import "swiper/css";
import { useLanguage } from "../../context/LanguageContext";
import africanWomanAvatar from "../../assets/images/avatars/african-woman.png";
import { Loader2, Trash2 } from "lucide-react";
import PrimaryButton from "../../components/PrimaryButton";

export default function CharacterGalleryCarousel({
  allImages,
  galleryImages = [],
  characterName,
  currentImageIndex,
  onSlideChange,
  onImageClick,
  generating,
  onGenerate,
  swiperRef,
  currentUserId,
  isAdmin,
  onDeleteImage,
  pendingSlideTo,
  onSlideToDone,
  refreshing = false,
  canGenerateImage = false,
}) {
  const { t } = useLanguage();

  // Delete confirmation
  const [deleteConfirmId, setDeleteConfirmId] = useState(null);

  // Generate dialog
  const [showGenerateDialog, setShowGenerateDialog] = useState(false);
  const [extraPrompt, setExtraPrompt] = useState("");

  const canDeleteImage = (imgData) => {
    if (!imgData) return false;
    if (isAdmin) return true;
    if (currentUserId && imgData.created_by === currentUserId) return true;
    return false;
  };

  const handleDeleteClick = (e, imgData) => {
    e.stopPropagation();
    if (imgData?.id) setDeleteConfirmId(imgData.id);
  };

  const confirmDelete = () => {
    if (deleteConfirmId) {
      onDeleteImage(deleteConfirmId);
      setDeleteConfirmId(null);
    }
  };

  const cancelDelete = () => setDeleteConfirmId(null);

  // Drop the dialog if the session ends while it is open, so a logout cannot
  // leave a control on screen that the backend would reject anyway.
  useEffect(() => {
    if (!canGenerateImage) setShowGenerateDialog(false);
  }, [canGenerateImage]);

  const openGenerateDialog = () => {
    if (!canGenerateImage) return;
    setExtraPrompt("");
    setShowGenerateDialog(true);
  };

  const handleGenerateConfirmed = () => {
    setShowGenerateDialog(false);
    if (!canGenerateImage) return;
    onGenerate(extraPrompt);
  };

  return (
    <>
      {/*
        - On mobile: full width with px-4, centered carousel column.
        - On desktop: no horizontal padding here (parent two-column
          layout in CharacterDetail handles padding) so the carousel
          can fill its left column. The 280px cap stays on mobile and
          is relaxed to 340px on desktop so the portrait doesn't blow
          up on wide screens.
      */}
      <div className="mt-6 px-4 md:px-0 w-full">
        <div className="w-full max-w-[280px] md:max-w-[340px] mx-auto md:mx-0">
          {/* ---- Carousel container ---- */}
          <div className="relative rounded-xl border border-white/5 overflow-hidden aspect-[3/4] bg-zinc-200 dark:bg-zinc-800 p-1">
            <Swiper
              key={allImages.length}
              spaceBetween={0}
              slidesPerView={1}
              onSlideChange={(swiper) => onSlideChange(swiper.activeIndex)}
              onSwiper={(swiper) => {
                swiperRef.current = swiper;
                if (pendingSlideTo !== null && pendingSlideTo !== undefined) {
                  swiper.slideTo(pendingSlideTo);
                  onSlideToDone?.();
                }
              }}
              className="w-full h-full"
            >
              {allImages.map((url, idx) => {
                const isAvatar = idx === 0;
                const imgData = isAvatar ? null : galleryImages[idx - 1];
                const showDelete = !isAvatar && canDeleteImage(imgData);

                return (
                  <SwiperSlide key={idx} className="h-full relative">
                    <button
                      type="button"
                      onClick={() => onImageClick(idx)}
                      className="block w-full h-full cursor-pointer rounded-lg overflow-hidden"
                    >
                      <img
                        src={url || africanWomanAvatar}
                        alt={`${characterName} ${isAvatar ? "" : t("characters.gallery") + " " + idx}`}
                        className="w-full h-full object-cover select-none rounded-lg"
                        draggable={false}
                      />
                    </button>

                    {showDelete && (
                      <button
                        type="button"
                        onClick={(e) => handleDeleteClick(e, imgData)}
                        className="absolute top-2 right-2 z-20 inline-flex h-8 w-8 items-center justify-center rounded-full bg-black/50 backdrop-blur-sm border border-white/20 text-white hover:bg-red-600/80 transition"
                        title={t("characters.deleteImage")}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </SwiperSlide>
                );
              })}
            </Swiper>

            {/* Dots */}
            {allImages.length > 1 && (
              <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-1.5 z-10">
                {allImages.map((_, idx) => (
                  <span
                    key={idx}
                    className={`w-2 h-2 rounded-full transition ${
                      idx === currentImageIndex ? "bg-white" : "bg-white/50"
                    }`}
                  />
                ))}
              </div>
            )}

            {/* Delete confirmation overlay */}
            {deleteConfirmId && (
              <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/60 rounded-xl">
                <div className="bg-zinc-800 border border-white/10 rounded-xl p-4 mx-4 shadow-lg text-center">
                  <p className="text-sm text-white mb-3">
                    {t("characters.deleteImageConfirm")}
                  </p>
                  <div className="flex justify-center gap-3">
                    <button
                      onClick={cancelDelete}
                      className="px-4 py-1.5 rounded-lg text-xs font-medium bg-white/10 text-white hover:bg-white/20"
                    >
                      {t("common.cancel")}
                    </button>
                    <button
                      onClick={confirmDelete}
                      className="px-4 py-1.5 rounded-lg text-xs font-medium bg-red-600 text-white hover:bg-red-500"
                    >
                      {t("common.delete")}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Refreshing spinner overlay (after generation) */}
            {refreshing && (
              <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/40 rounded-xl">
                <Loader2 size={32} className="animate-spin text-white" />
              </div>
            )}
          </div>

          {/* Swipe hint */}
          {allImages.length > 1 && (
            <p className="text-xs text-center mt-1.5 theme-text-muted">
              {t("characters.swipeToSeeMore")}
            </p>
          )}

          {/* Generation is authenticated-only: guests get no control at all,
              not a disabled one or a login prompt. */}
          {canGenerateImage && (
            <>
              <div className="flex justify-center mt-3">
                <button
                  onClick={openGenerateDialog}
                  disabled={generating}
                  className="inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-medium transition disabled:opacity-60"
                  style={{
                    backgroundColor: "var(--color-accent-soft)",
                    color: "var(--color-accent)",
                    border: "1px solid var(--color-accent-soft)",
                  }}
                >
                  {generating ? (
                    <>
                      <Loader2 size={16} className="animate-spin" />
                      {t("characters.generating")}
                    </>
                  ) : (
                    t("characters.generateNewImage")
                  )}
                </button>
              </div>

              {/* Privacy explanation */}
              <p className="text-[11px] text-center mt-2 theme-text-muted leading-relaxed">
                {t("characters.imagePrivacyNotice")}
              </p>
            </>
          )}
        </div>
      </div>

      {/* ---- Generate prompt dialog (fixed overlay) ---- */}
      {showGenerateDialog && canGenerateImage && (
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
              value={extraPrompt}
              onChange={(e) => setExtraPrompt(e.target.value)}
              placeholder={t("characters.extraDetailsPlaceholder")}
              className="w-full rounded-xl border px-4 py-3 text-sm theme-text theme-input resize-none"
              style={{
                backgroundColor: "var(--color-input)",
                borderColor: "var(--color-border)",
              }}
            />
            <div className="flex justify-end gap-3 mt-4">
              <button
                onClick={() => setShowGenerateDialog(false)}
                className="rounded-xl px-4 py-2 text-sm font-medium theme-text-muted hover:theme-soft"
              >
                {t("common.cancel")}
              </button>
              <PrimaryButton onClick={handleGenerateConfirmed}>
                {t("characters.generate")}
              </PrimaryButton>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
