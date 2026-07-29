import { MapPin, Activity, Edit, MessageCircle } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext";
import africanWomanAvatar from "../../assets/images/avatars/african-woman.png";
import PrimaryButton from "../../components/PrimaryButton";

export default function CharacterDetailHeader({
  character,
  canEdit,
  onEdit,
  onStartChat,
}) {
  const { t } = useLanguage();
  const imageSrc = character.avatar_url || africanWomanAvatar;
  const { town, country, age } = character;

  return (
    <>
      {/* Title row */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold theme-text">{character.name}</h1>
          <p className="mt-1 text-sm theme-text-muted">
            {character.short_description}
          </p>
        </div>
        {canEdit && (
          <button
            onClick={onEdit}
            className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition"
            style={{
              backgroundColor: "var(--color-soft)",
              color: "var(--color-text)",
              border: "1px solid var(--color-border)",
            }}
          >
            <Edit size={16} />
            {t("common.edit")}
          </button>
        )}
      </div>

      {/* Avatar + quick facts */}
      <div className="rounded-2xl border p-4 theme-card">
        <div className="flex flex-col items-center gap-4 sm:flex-row">
          <img
            src={imageSrc}
            alt={character.name}
            className="h-48 w-48 rounded-2xl object-cover border-2 border-cyan-400/30"
          />
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-2 text-sm theme-text-muted">
              <MapPin size={16} />
              {town && country
                ? `${town}, ${country}`
                : country || town || t("characters.locationNotSpecified")}
            </div>
            <div className="flex items-center gap-2 text-sm theme-text-muted">
              <Activity size={16} />
              {t("characters.ageWithYears", { age })}
            </div>
            {character.is_active && (
              <PrimaryButton
                onClick={onStartChat}
                className="whitespace-nowrap inline-flex items-center"
              >
                <MessageCircle size={16} className="mr-2" />
                {t("characters.startChat")}
              </PrimaryButton>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
