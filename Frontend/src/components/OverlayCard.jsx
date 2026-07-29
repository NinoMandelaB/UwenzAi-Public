// src/components/OverlayCard.jsx

import PageCard from "./PageCard";

export default function OverlayCard({
  image,
  title,
  subtitle,
  topRight,
  tags = [],
  tagsContent,
  children,
  footer,
  imageAlt,
  imageHeight = "h-56",
  className = "",
  onImageError,
  onImageClick,
  contentClassName = "space-y-3 p-4", // ← new prop
}) {
  return (
    <PageCard className={`overflow-hidden rounded-2xl p-0 ${className}`}>
      <div
        className={`relative w-full overflow-hidden rounded-2xl ${imageHeight}`}
      >
        {/* Clickable image */}
        {onImageClick ? (
          <button
            type="button"
            onClick={onImageClick}
            className="absolute inset-0 h-full w-full cursor-pointer border-0 p-0"
            aria-label={`View ${imageAlt || title}`}
          >
            <img
              src={image}
              alt={imageAlt || title}
              onError={onImageError}
              className="absolute inset-0 h-full w-full object-cover"
            />
          </button>
        ) : (
          <img
            src={image}
            alt={imageAlt || title}
            onError={onImageError}
            className="absolute inset-0 h-full w-full object-cover"
          />
        )}

        {/* Gradient overlays */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-black/18 to-black/5 pointer-events-none" />
        <div className="absolute inset-x-0 bottom-0 h-[48%] bg-gradient-to-t from-black/40 via-black/12 to-transparent pointer-events-none" />

        {topRight && (
          <div className="absolute right-3 top-3 z-10 flex flex-wrap justify-end gap-2">
            {topRight}
          </div>
        )}

        <div className="absolute bottom-0 left-0 right-0 z-10 p-4">
          <h3
            className="text-lg font-semibold text-white"
            style={{ textShadow: "0 2px 12px rgba(0,0,0,0.45)" }}
          >
            {title}
          </h3>
          {subtitle && (
            <p
              className="mt-1 text-sm text-white/90"
              style={{ textShadow: "0 2px 10px rgba(0,0,0,0.4)" }}
            >
              {subtitle}
            </p>
          )}
          {tagsContent ? (
            <div className="mt-3 flex flex-wrap gap-2">{tagsContent}</div>
          ) : tags.length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {tags.map((tag, idx) => (
                <span
                  key={`${tag}-${idx}`}
                  className="rounded-full border border-white/20 bg-black/28 px-3 py-1 text-[11px] text-white backdrop-blur-md"
                >
                  {tag}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      {(children || footer) && (
        <div className={contentClassName}>
          {children}
          {footer}
        </div>
      )}
    </PageCard>
  );
}
