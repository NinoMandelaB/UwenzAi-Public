// src/components/CharacterCard.jsx

import { Pencil, Trash2, MapPin, Activity, MessageCircle } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useChatModal } from "../context/ChatModalContext";
import africanWomanAvatar from "../assets/images/avatars/african-woman.png";

export default function CharacterCard({
  character,
  isAdmin = false,
  canDelete = false,
  onDelete,
  onImageError,
  onImageLoad,
}) {
  const navigate = useNavigate();
  const { t } = useLanguage();
  const { openChat } = useChatModal();

  const {
    characterId,
    id,
    avatarUrl,
    name,
    town,
    country,
    age,
    role,
    shortDescription,
    occupation,
  } = character;
  const locationStr = [town, country].filter(Boolean).join(", ") || null;

  const handleView = () => navigate(`/characters/${characterId}`);
  const handleEdit = () => navigate(`/characters/${characterId}/edit`);

  const handleStartChat = (e) => {
    e.stopPropagation();
    const characterData = {
      id: characterId || id,
      name,
      avatar: avatarUrl,
      role: role || shortDescription,
    };
    console.log("💬 Opening chat from card:", characterData);
    openChat(characterData);
  };

  // Show the edit button for admins (can edit any character) OR for the
  // character's creator (canDelete is already set to true for owners in
  // Characters.jsx via user_id === createdByUserId).
  const canEdit = isAdmin || canDelete;

  return (
    <div className="w-full">
      <div className="relative h-80 rounded-2xl overflow-hidden shadow-lg group/image">
        {/* Clickable image background (detail view) */}
        <button
          type="button"
          onClick={handleView}
          className="absolute inset-0 h-full w-full cursor-pointer border-0 p-0"
        >
          <img
            src={avatarUrl || africanWomanAvatar}
            alt={name}
            onError={onImageError}
            onLoad={onImageLoad}
            className="absolute inset-0 h-full w-full object-cover"
          />
        </button>

        {/* Gradient overlays */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent pointer-events-none" />
        <div className="absolute inset-y-0 right-0 w-1/3 bg-gradient-to-l from-black/30 to-transparent pointer-events-none" />

        {/* Online pill – top right */}
        <div className="absolute top-3 right-3 z-10">
          <span className="inline-flex items-center gap-2 rounded-full border border-emerald-400/30 bg-slate-900/70 px-3 py-1 text-xs font-medium text-white backdrop-blur-md">
            <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.9)]" />
            {t("common.online")}
          </span>
        </div>

        {/* Chat button – right side, vertically centered */}
        <div className="absolute top-1/2 right-3 z-10 -translate-y-1/2 flex flex-col items-center gap-4">
          <div className="flex flex-col items-center gap-0.5">
            <button
              type="button"
              onClick={handleStartChat}
              className="flex h-10 w-10 items-center justify-center rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-white hover:bg-white/20 transition"
              title={t("common.chat")}
            >
              <MessageCircle size={18} />
            </button>
            <span className="text-[9px] leading-none text-white/70">
              {t("common.chat")}
            </span>
          </div>
        </div>

        {/* Bottom area: name, occupation, description, location & age */}
        <div className="absolute bottom-0 left-0 right-0 z-10 p-4">
          <h3 className="text-xl font-bold text-white leading-tight mb-1">
            {name}
          </h3>

          {/* Occupation – just below the name, no pill, no icon */}
          {occupation && (
            <p className="text-xs text-white/80 mb-1 font-semibold">
              {t("characters.occupationLabel")}{" "}
              {typeof occupation === "string" ? occupation : occupation.name}
            </p>
          )}

          {/* Short description – one-liner, truncated */}
          {shortDescription && (
            <p className="text-xs text-white/80 line-clamp-1 mb-2">
              {shortDescription}
            </p>
          )}

          <div className="flex items-center justify-between text-sm text-white/90">
            {locationStr && (
              <span className="flex items-center gap-1 truncate">
                <MapPin size={14} className="shrink-0" />
                <span className="truncate">{locationStr}</span>
              </span>
            )}
            {age != null && (
              <span className="flex items-center gap-1 shrink-0">
                <Activity size={14} className="shrink-0" />
                {t("characters.ageWithYears", { age })}
              </span>
            )}
          </div>
        </div>

        {/* Edit / Delete – top left on hover */}
        {(canEdit || canDelete) && (
          <div className="absolute top-3 left-3 z-20 flex items-center gap-2 opacity-0 group-hover/image:opacity-100 transition-opacity">
            {canEdit && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleEdit();
                }}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full border text-white hover:scale-105"
                style={{
                  backgroundColor: "rgba(15,23,42,0.62)",
                  borderColor: "rgba(255,255,255,0.18)",
                  backdropFilter: "blur(8px)",
                }}
                aria-label={t("characters.editCharacter", { name })}
              >
                <Pencil size={15} />
              </button>
            )}
            {canDelete && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete?.();
                }}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full border text-white hover:scale-105"
                style={{
                  backgroundColor: "rgba(127,29,29,0.78)",
                  borderColor: "rgba(251,113,133,0.28)",
                  backdropFilter: "blur(8px)",
                }}
                aria-label={t("characters.deleteCharacter", { name })}
              >
                <Trash2 size={15} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
