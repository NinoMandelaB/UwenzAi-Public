import { useEffect, useState } from "react";
import {
  X,
  Sparkles,
  MessageCircle,
  Shield,
  Globe,
  Flame,
  CheckCircle,
  MapPin,
  User,
  Briefcase,
  Heart,
  Palette,
  Scissors,
  Activity,
  Image,
} from "lucide-react";
import africanWomanAvatar from "../assets/images/avatars/african-woman.png";
import GalleryViewer from "../components/GalleryViewer";
import { listHobbies } from "../services/characterService";
import { CLOTHING_LABELS_EN } from "../config/clothingStyles";

function DetailChip({ icon, label, value }) {
  return (
    <div
      className="rounded-2xl border p-3"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-soft)",
      }}
    >
      <div className="flex items-start gap-3">
        <div
          className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
          style={{
            backgroundColor: "var(--color-accent-soft)",
            color: "var(--color-accent)",
          }}
        >
          {icon}
        </div>
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide theme-text-muted">
            {label}
          </p>
          <p className="mt-1 break-words text-sm font-medium theme-text">
            {value || "—"}
          </p>
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ icon, title }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <span style={{ color: "var(--color-accent)" }}>{icon}</span>
      <h4 className="text-sm font-semibold theme-text">{title}</h4>
    </div>
  );
}

function getReadableCategory(category) {
  if (!category) return "Not specified";
  const cat = category.toLowerCase();
  const map = { supportive: "Supportive", fun: "Fun", romantic: "Romantic" };
  return map[cat] || category.charAt(0).toUpperCase() + category.slice(1);
}

function getReadableLanguage(language) {
  if (!language) return "Not specified";
  const lang = language.toLowerCase();
  const map = {
    en: "English",
    sw: "Swahili",
    sh: "Sheng",
    multi: "Multilingual",
  };
  return map[lang] || language.toUpperCase();
}

function formatChoice(value, choicesMap) {
  return choicesMap[value] || value?.replace(/_/g, " ") || "—";
}

const GENDER_DISPLAY = {
  male: "Male",
  female: "Female",
};

const VISUAL_STYLE_DISPLAY = {
  realistic: "Realistic",
  anime: "Anime",
  cartoon: "Cartoon",
};

const SKIN_COLOR_DISPLAY = {
  fair: "Fair",
  light: "Light",
  medium: "Medium",
  olive: "Olive",
  tan: "Tan",
  brown: "Brown",
  dark: "Dark",
  ebony: "Ebony",
};

const HAIRSTYLE_DISPLAY = {
  short: "Short",
  medium: "Medium",
  long: "Long",
  curly: "Curly",
  wavy: "Wavy",
  straight: "Straight",
  bald: "Bald",
  buzz_cut: "Buzz Cut",
  ponytail: "Ponytail",
  braids: "Braids",
  dreadlocks: "Dreadlocks",
  afro: "Afro",
};

const HAIR_COLOR_DISPLAY = {
  black: "Black",
  brown: "Brown",
  blonde: "Blonde",
  red: "Red",
  gray: "Gray",
  white: "White",
  blue: "Blue",
  pink: "Pink",
  green: "Green",
  purple: "Purple",
};

const BODY_TYPE_DISPLAY = {
  slim: "Slim",
  athletic: "Athletic",
  average: "Average",
  curvy: "Curvy",
  muscular: "Muscular",
  chubby: "Chubby",
  petite: "Petite",
  tall: "Tall",
};

const BREAST_SIZE_DISPLAY = {
  small: "Small",
  medium: "Medium",
  large: "Large",
  extra_large: "Extra Large",
  na: "N/A",
};

const BUTT_SIZE_DISPLAY = {
  small: "Small",
  medium: "Medium",
  large: "Large",
  extra_large: "Extra Large",
  na: "N/A",
};

const CHEST_HAIR_DISPLAY = {
  none: "None",
  light: "Light",
  medium: "Medium",
  heavy: "Heavy",
  na: "N/A",
};

const MUSCLE_DEFINITION_DISPLAY = {
  slim: "Slim",
  toned: "Toned",
  muscular: "Muscular",
  bulky: "Bulky",
  na: "N/A",
};

const BEARD_STYLE_DISPLAY = {
  clean_shaven: "Clean Shaven",
  stubble: "Stubble",
  full_beard: "Full Beard",
  goatee: "Goatee",
  mustache: "Mustache",
  na: "N/A",
};

const PERSONALITY_DISPLAY = {
  witty: "Witty",
  empathetic: "Empathetic",
  adventurous: "Adventurous",
  shy: "Shy",
  confident: "Confident",
  romantic: "Romantic",
  funny: "Funny",
  intellectual: "Intellectual",
  caring: "Caring",
  mysterious: "Mysterious",
  friendly: "Friendly",
};

const RELATIONSHIP_DISPLAY = {
  friend: "Friend",
  boyfriend: "Boyfriend",
  girlfriend: "Girlfriend",
  mentor: "Mentor",
  companion: "Companion",
  flirty: "Flirty",
  romantic: "Romantic",
};

const CLOTHING_DISPLAY = CLOTHING_LABELS_EN;

export default function CharacterDetailsModal({
  isOpen,
  onClose,
  onStartChat,
  onApprove,
  character,
  isAdminView = false,
}) {
  const [hobbyMap, setHobbyMap] = useState({});

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") onClose?.();
    };

    document.addEventListener("keydown", handleKeyDown);
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;
    async function fetchHobbies() {
      try {
        const res = await listHobbies();
        const map = {};
        res?.data?.hobbies?.forEach((h) => {
          map[h.id] = h.name;
        });
        setHobbyMap(map);
      } catch (e) {
        console.error("Failed to load hobbies:", e);
      }
    }
    fetchHobbies();
  }, [isOpen]);

  if (!isOpen || !character) return null;

  const imageSrc = character.avatarUrl || africanWomanAvatar;
  const categoryLabel = getReadableCategory(character.category);
  const languageLabel = getReadableLanguage(character.primaryLanguage);
  const memoryEnabled = character.memoryEnabled ?? true;

  const gender = character.gender;
  const visualStyle = character.visual_style;
  const country = character.country;
  const town = character.town;
  const age = character.age;
  const skinColor = character.skin_color;
  const hairstyle = character.hairstyle;
  const hairColor = character.hair_color;
  const bodyType = character.body_type;
  const breastSize = character.breast_size;
  const buttSize = character.butt_size;
  const chestHair = character.chest_hair;
  const muscleDefinition = character.muscle_definition;
  const beardStyle = character.beard_style;
  const personality = character.personality;
  const occupation = character.occupation;
  const hobbies = character.hobby_ids || [];
  const relationshipStyle = character.relationship_style;
  const clothingStyle = character.clothing_style;

  const showFemaleAttrs = gender === "female";
  const showMaleAttrs = gender === "male";

  return (
    <div
      className="fixed inset-0 z-[120] flex items-end justify-center bg-black/60 px-4 py-4 backdrop-blur-sm sm:items-center"
      onClick={onClose}
    >
      <div
        className="max-h-[92vh] w-full max-w-2xl overflow-hidden rounded-[2rem] border shadow-2xl"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-card)",
        }}
        onClick={(event) => event.stopPropagation()}
      >
        {/* Header Image Section */}
        <div className="relative h-64 w-full overflow-hidden sm:h-72">
          <img
            src={imageSrc}
            alt={character.name}
            className="absolute inset-0 h-full w-full object-cover"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />

          <div className="absolute right-4 top-4 z-10">
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border text-white transition hover:bg-white/10"
              style={{
                borderColor: "rgba(255,255,255,0.2)",
                backgroundColor: "rgba(0,0,0,0.4)",
                backdropFilter: "blur(8px)",
              }}
            >
              <X size={20} />
            </button>
          </div>

          <div className="absolute bottom-0 left-0 right-0 p-6">
            <div className="flex flex-wrap gap-2">
              <span
                className="rounded-full px-3 py-1 text-[10px] font-bold uppercase tracking-wider backdrop-blur-md"
                style={{
                  backgroundColor:
                    character.review_status === "approved"
                      ? "rgba(16,185,129,0.2)"
                      : "rgba(251,191,36,0.2)",
                  color:
                    character.review_status === "approved"
                      ? "rgb(52,211,153)"
                      : "rgb(251,191,36)",
                  border:
                    character.review_status === "approved"
                      ? "1px solid rgba(52,211,153,0.3)"
                      : "1px solid rgba(251,191,36,0.3)",
                }}
              >
                {character.review_status === "approved"
                  ? "APPROVED"
                  : "REVIEWING"}
              </span>
            </div>
            <h3 className="mt-3 text-3xl font-bold text-white">
              {character.name}
            </h3>
            <p className="mt-1 text-sm text-white/80 line-clamp-2">
              {character.role || character.short_description}
            </p>
          </div>
        </div>

        {/* Details Section */}
        <div className="max-h-[calc(92vh-18rem)] overflow-y-auto p-6">
          {/* Quick Info Chips */}
          <div className="grid gap-3 sm:grid-cols-2">
            <DetailChip
              icon={<Flame size={18} />}
              label="Mood"
              value={character.mood || "Neutral"}
            />
            <DetailChip
              icon={<Sparkles size={18} />}
              label="Category"
              value={categoryLabel}
            />
            <DetailChip
              icon={<Globe size={18} />}
              label="Language"
              value={languageLabel}
            />
            <DetailChip
              icon={<Shield size={18} />}
              label="Memory"
              value={memoryEnabled ? "Enabled" : "Disabled"}
            />
          </div>

          {/* Introduction */}
          {character.intro && (
            <div className="mt-6 rounded-2xl border p-4 theme-border bg-soft">
              <p className="text-xs font-bold uppercase tracking-widest theme-text-muted mb-2">
                Introduction
              </p>
              <p className="text-sm leading-relaxed theme-text">
                {character.intro}
              </p>
            </div>
          )}

          {/* Location Section */}
          {(country || town) && (
            <div className="mt-6">
              <SectionTitle icon={<MapPin size={18} />} title="Location" />
              <div className="rounded-2xl border p-4 theme-border bg-soft">
                <p className="text-sm theme-text">
                  {[town, country].filter(Boolean).join(", ") ||
                    "Not specified"}
                </p>
              </div>
            </div>
          )}

          {/* Gallery Section */}
          {character.character_id && (
            <div className="mt-6">
              <SectionTitle icon={<Image size={18} />} title="Gallery" />
              <div className="rounded-2xl border p-4 theme-border bg-soft">
                <GalleryViewer characterId={character.character_id} />
              </div>
            </div>
          )}

          {/* Appearance Section */}
          {(gender || age || visualStyle) && (
            <div className="mt-6">
              <SectionTitle icon={<User size={18} />} title="Appearance" />
              <div className="grid gap-3 sm:grid-cols-2">
                {gender && (
                  <DetailChip
                    icon={<User size={16} />}
                    label="Gender"
                    value={formatChoice(gender, GENDER_DISPLAY)}
                  />
                )}
                {age && (
                  <DetailChip
                    icon={<Activity size={16} />}
                    label="Age"
                    value={`${age} years`}
                  />
                )}
                {visualStyle && (
                  <DetailChip
                    icon={<Palette size={16} />}
                    label="Art Style"
                    value={formatChoice(visualStyle, VISUAL_STYLE_DISPLAY)}
                  />
                )}
                {skinColor && (
                  <DetailChip
                    icon={<Palette size={16} />}
                    label="Skin Tone"
                    value={formatChoice(skinColor, SKIN_COLOR_DISPLAY)}
                  />
                )}
                {hairstyle && (
                  <DetailChip
                    icon={<Scissors size={16} />}
                    label="Hairstyle"
                    value={formatChoice(hairstyle, HAIRSTYLE_DISPLAY)}
                  />
                )}
                {hairColor && (
                  <DetailChip
                    icon={<Palette size={16} />}
                    label="Hair Color"
                    value={formatChoice(hairColor, HAIR_COLOR_DISPLAY)}
                  />
                )}
                {bodyType && (
                  <DetailChip
                    icon={<Activity size={16} />}
                    label="Body Type"
                    value={formatChoice(bodyType, BODY_TYPE_DISPLAY)}
                  />
                )}
                {showFemaleAttrs && breastSize && breastSize !== "na" && (
                  <DetailChip
                    icon={<Heart size={16} />}
                    label="Breast Size"
                    value={formatChoice(breastSize, BREAST_SIZE_DISPLAY)}
                  />
                )}
                {showFemaleAttrs && buttSize && buttSize !== "na" && (
                  <DetailChip
                    icon={<Heart size={16} />}
                    label="Butt Size"
                    value={formatChoice(buttSize, BUTT_SIZE_DISPLAY)}
                  />
                )}
                {showMaleAttrs && chestHair && chestHair !== "na" && (
                  <DetailChip
                    icon={<Activity size={16} />}
                    label="Chest Hair"
                    value={formatChoice(chestHair, CHEST_HAIR_DISPLAY)}
                  />
                )}
                {showMaleAttrs &&
                  muscleDefinition &&
                  muscleDefinition !== "na" && (
                    <DetailChip
                      icon={<Activity size={16} />}
                      label="Muscle Definition"
                      value={formatChoice(
                        muscleDefinition,
                        MUSCLE_DEFINITION_DISPLAY,
                      )}
                    />
                  )}
                {showMaleAttrs && beardStyle && beardStyle !== "na" && (
                  <DetailChip
                    icon={<User size={16} />}
                    label="Beard Style"
                    value={formatChoice(beardStyle, BEARD_STYLE_DISPLAY)}
                  />
                )}
              </div>
            </div>
          )}

          {/* Personality & Lifestyle Section */}
          {(personality ||
            occupation ||
            relationshipStyle ||
            clothingStyle) && (
            <div className="mt-6">
              <SectionTitle
                icon={<Sparkles size={18} />}
                title="Personality & Lifestyle"
              />
              <div className="grid gap-3 sm:grid-cols-2">
                {personality && (
                  <DetailChip
                    icon={<Sparkles size={16} />}
                    label="Personality"
                    value={formatChoice(personality, PERSONALITY_DISPLAY)}
                  />
                )}
                {occupation && (
                  <DetailChip
                    icon={<Briefcase size={16} />}
                    label="Occupation"
                    value={occupation.name || occupation}
                  />
                )}
                {relationshipStyle && (
                  <DetailChip
                    icon={<Heart size={16} />}
                    label="Relationship"
                    value={formatChoice(
                      relationshipStyle,
                      RELATIONSHIP_DISPLAY,
                    )}
                  />
                )}
                {clothingStyle && (
                  <DetailChip
                    icon={<Palette size={16} />}
                    label="Clothing"
                    value={formatChoice(clothingStyle, CLOTHING_DISPLAY)}
                  />
                )}
              </div>
              {hobbies && hobbies.length > 0 && (
                <div className="mt-3 rounded-2xl border p-4 theme-border bg-soft">
                  <p className="text-xs font-medium theme-text-muted mb-2">
                    Hobbies
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {hobbies.map((hobbyId) => (
                      <span
                        key={hobbyId}
                        className="rounded-full border px-3 py-1 text-xs theme-text"
                        style={{ borderColor: "var(--color-border)" }}
                      >
                        {hobbyMap[hobbyId] || hobbyId}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Action Footer */}
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            {isAdminView ? (
              <>
                <button
                  type="button"
                  onClick={() => {
                    onApprove?.(character.id || character.characterId);
                    onClose();
                  }}
                  className="inline-flex flex-1 items-center justify-center gap-2 rounded-full bg-emerald-600 py-4 text-sm font-bold text-white transition hover:bg-emerald-500 shadow-lg shadow-emerald-900/20"
                >
                  <CheckCircle size={18} />
                  Approve Character
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="inline-flex flex-1 items-center justify-center rounded-full border py-4 text-sm font-bold theme-text theme-border transition hover:bg-white/5"
                >
                  Go Back
                </button>
              </>
            ) : (
              <>
                {onStartChat && (
                  <button
                    type="button"
                    onClick={() => onStartChat?.(character)}
                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-full border px-4 py-4 text-sm font-bold transition hover:opacity-90 active:scale-[0.98]"
                    style={{
                      borderColor: "rgba(34, 211, 238, 0.45)",
                      background:
                        "linear-gradient(180deg, rgba(34, 211, 238, 0.15) 0%, rgba(34, 211, 238, 0.08) 100%)",
                      color: "rgb(34, 211, 238)",
                      backdropFilter: "blur(6px)",
                    }}
                  >
                    <MessageCircle size={18} />
                    Start Chat
                  </button>
                )}
                <button
                  type="button"
                  onClick={onClose}
                  className="inline-flex flex-1 items-center justify-center rounded-full border py-4 text-sm font-bold theme-text theme-border transition hover:bg-white/5"
                >
                  Close
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
