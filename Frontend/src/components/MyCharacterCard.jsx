// src/components/MyCharacterCard.jsx
import {
  Pencil,
  Trash2,
  MapPin,
  Activity,
  CheckCircle,
  Clock,
  XCircle,
  AlertCircle,
  MessageCircle,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { useChatModal } from "../context/ChatModalContext";
import africanWomanAvatar from "../assets/images/avatars/african-woman.png";

const STATUS_CONFIG = {
  draft: {
    label: "Draft",
    icon: Clock,
    className: "bg-slate-600/80 text-slate-100 border-slate-400/30",
  },
  pending: {
    label: "Pending Review",
    icon: AlertCircle,
    className: "bg-amber-500/80 text-amber-100 border-amber-400/30",
  },
  approved: {
    label: "Approved",
    icon: CheckCircle,
    className: "bg-emerald-500/80 text-emerald-100 border-emerald-400/30",
  },
  rejected: {
    label: "Rejected",
    icon: XCircle,
    className: "bg-rose-500/80 text-rose-100 border-rose-400/30",
  },
  archived: {
    label: "Archived",
    icon: Clock,
    className: "bg-gray-500/80 text-gray-100 border-gray-400/30",
  },
};

export default function MyCharacterCard({
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
    reviewStatus,
    rejectionReason,
  } = character;

  const locationStr = [town, country].filter(Boolean).join(", ") || null;
  const statusKey = reviewStatus?.toLowerCase() || "draft";
  const status = STATUS_CONFIG[statusKey] || STATUS_CONFIG.draft;
  const StatusIcon = status.icon;

  // Block chat only for rejected and archived
  const isChatBlocked = statusKey === "rejected" || statusKey === "archived";

  const handleView = () => navigate(`/characters/${characterId}`);
  const handleEdit = () => navigate(`/characters/${characterId}/edit`);

  const handleStartChat = (e) => {
    e.stopPropagation();
    if (isChatBlocked) return;
    const characterData = {
      id: characterId || id,
      name,
      avatar: avatarUrl,
      role: role || shortDescription,
    };
    openChat(characterData);
  };

  const canEdit = isAdmin || canDelete;

  return (
    <div className="w-full">
      <div className="relative h-80 rounded-2xl overflow-hidden shadow-lg group/image">
        {/* Clickable image background */}
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

        {/* Status Badge – top right */}
        <div className="absolute top-3 right-3 z-10">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium backdrop-blur-md ${status.className}`}
          >
            <StatusIcon size={14} className="shrink-0" />
            {t(`characters.status_${statusKey}`) || status.label}
          </span>
        </div>

        {/* Chat button – blocked only for rejected and archived */}
        <div className="absolute top-1/2 right-3 z-10 -translate-y-1/2 flex flex-col items-center gap-0.5">
          <button
            type="button"
            onClick={handleStartChat}
            disabled={isChatBlocked}
            className={`flex h-10 w-10 items-center justify-center rounded-full backdrop-blur-md border transition ${
              isChatBlocked
                ? "bg-white/5 border-white/10 text-white/40 cursor-not-allowed"
                : "bg-white/10 border-white/20 text-white hover:bg-white/20"
            }`}
            title={isChatBlocked ? t("common.chatBlocked") : t("common.chat")}
          >
            <MessageCircle size={18} />
          </button>
          <span
            className={`text-[9px] leading-none ${
              isChatBlocked ? "text-white/40" : "text-white/70"
            }`}
          >
            {isChatBlocked ? t("common.chatBlocked") : t("common.chat")}
          </span>
        </div>

        {/* Bottom area: name, occupation, description, location & age */}
        <div className="absolute bottom-0 left-0 right-0 z-10 p-4">
          <h3 className="text-xl font-bold text-white leading-tight mb-1">
            {name}
          </h3>

          {occupation && (
            <p className="text-xs text-white/80 mb-1 font-semibold">
              {t("characters.occupationLabel")}{" "}
              {typeof occupation === "string" ? occupation : occupation.name}
            </p>
          )}

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

          {/* Rejection reason – only shown if status is rejected */}
          {statusKey === "rejected" && rejectionReason && (
            <div className="mt-2 text-xs text-rose-300/80 bg-rose-900/30 backdrop-blur-sm rounded-lg px-2 py-1 border border-rose-500/20">
              <span className="font-medium">Rejected:</span> {rejectionReason}
            </div>
          )}
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
