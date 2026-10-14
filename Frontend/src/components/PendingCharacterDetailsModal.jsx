// src/components/PendingCharacterDetailsModal.jsx
import { useState } from "react";
import {
  X,
  User,
  MapPin,
  Calendar,
  Palette,
  Eye,
  Scissors,
  Activity,
  Heart,
  Briefcase,
  Sparkles,
  BookOpen,
  MessageSquare,
  Globe,
  Image,
  CheckCircle,
  XCircle,
  Info,
} from "lucide-react";
import { useLanguage } from "../context/LanguageContext";
import placeholderAvatar from "../assets/images/avatars/placeholder.png";
import { CLOTHING_LABELS_EN } from "../config/clothingStyles";

function DetailRow({ label, value, icon: Icon }) {
  if (!value && value !== 0) return null;
  return (
    <div className="flex items-start gap-3 py-2 border-b border-[var(--color-border)] last:border-0">
      {Icon && (
        <Icon size={18} className="mt-0.5 text-[var(--color-text-muted)]" />
      )}
      <div className="flex-1">
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] block">
          {label}
        </span>
        <span className="text-sm font-medium text-[var(--color-text)] break-words">
          {value}
        </span>
      </div>
    </div>
  );
}

function Section({ title, children }) {
  if (!children || (Array.isArray(children) && children.every((c) => !c)))
    return null;
  return (
    <div className="mb-6">
      <h4 className="text-sm font-bold uppercase tracking-wider text-[var(--color-accent)] mb-3 border-b border-[var(--color-border)] pb-2">
        {title}
      </h4>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

// Helper: get display string from a value and a choices map
function getDisplay(value, choicesMap) {
  if (!value) return null;
  if (choicesMap && typeof choicesMap === "object") {
    return choicesMap[value] || value;
  }
  return value;
}

// Display maps
const GENDER_DISPLAY = { male: "Male", female: "Female" };
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

export default function PendingCharacterDetailsModal({
  isOpen,
  character,
  onClose,
  onApprove,
  onReject,
  isProcessing = false,
}) {
  const { t } = useLanguage();
  const [rejectionReason, setRejectionReason] = useState("");
  const [showRejectInput, setShowRejectInput] = useState(false);

  if (!isOpen || !character) return null;

  const {
    name,
    short_description,
    mood,
    category,
    intro,
    greeting_message,
    tone_style,
    primary_language,
    avatar_url,
    gender,
    age,
    country,
    town,
    visual_style,
    skin_color,
    eye_color,
    hairstyle,
    hair_color,
    body_type,
    breast_size,
    butt_size,
    chest_hair,
    muscle_definition,
    beard_style,
    personality_description,
    personalities = [],
    occupation,
    hobbies = [],
    relationship_style,
    clothing_style,
    system_instructions,
    prompt_template,
    created_by,
    created_at,
    review_status,
    rejection_reason: existingRejectionReason,
  } = character;

  const formatDate = (date) => {
    if (!date) return "—";
    return new Date(date).toLocaleString();
  };

  // Use the fixed getDisplay function
  const genderDisplay = getDisplay(gender, GENDER_DISPLAY);
  const visualStyleDisplay = getDisplay(visual_style, VISUAL_STYLE_DISPLAY);
  const skinColorDisplay = getDisplay(skin_color, SKIN_COLOR_DISPLAY);
  const hairstyleDisplay = getDisplay(hairstyle, HAIRSTYLE_DISPLAY);
  const hairColorDisplay = getDisplay(hair_color, HAIR_COLOR_DISPLAY);
  const bodyTypeDisplay = getDisplay(body_type, BODY_TYPE_DISPLAY);
  const breastSizeDisplay = getDisplay(breast_size, BREAST_SIZE_DISPLAY);
  const buttSizeDisplay = getDisplay(butt_size, BUTT_SIZE_DISPLAY);
  const chestHairDisplay = getDisplay(chest_hair, CHEST_HAIR_DISPLAY);
  const muscleDefinitionDisplay = getDisplay(
    muscle_definition,
    MUSCLE_DEFINITION_DISPLAY,
  );
  const beardStyleDisplay = getDisplay(beard_style, BEARD_STYLE_DISPLAY);
  const relationshipStyleDisplay = getDisplay(
    relationship_style,
    RELATIONSHIP_DISPLAY,
  );
  const clothingStyleDisplay = getDisplay(clothing_style, CLOTHING_DISPLAY);

  const personalityNames = personalities.map((p) => p.name || p).join(", ");
  const hobbyNames = hobbies.map((h) => h.name || h).join(", ");

  const showFemaleAttrs = gender === "female";
  const showMaleAttrs = gender === "male";

  const hasAppearance =
    gender ||
    age ||
    visualStyleDisplay ||
    skinColorDisplay ||
    eye_color ||
    hairstyleDisplay ||
    hairColorDisplay ||
    bodyTypeDisplay ||
    (showFemaleAttrs && breastSizeDisplay && breastSizeDisplay !== "N/A") ||
    (showFemaleAttrs && buttSizeDisplay && buttSizeDisplay !== "N/A") ||
    (showMaleAttrs && chestHairDisplay && chestHairDisplay !== "N/A") ||
    (showMaleAttrs &&
      muscleDefinitionDisplay &&
      muscleDefinitionDisplay !== "N/A") ||
    (showMaleAttrs && beardStyleDisplay && beardStyleDisplay !== "N/A");

  return (
    <div
      className="fixed inset-0 z-[130] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="relative max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-3xl shadow-2xl"
        style={{
          backgroundColor: "var(--color-shell)",
          border: "1px solid var(--color-border)",
        }}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 z-10 rounded-full p-2 hover:bg-[var(--color-soft)] transition"
          style={{ color: "var(--color-text-muted)" }}
        >
          <X size={22} />
        </button>

        <div className="p-6">
          {/* Header */}
          <div className="flex items-start gap-4 mb-6">
            <img
              src={avatar_url || placeholderAvatar}
              alt={name}
              className="h-20 w-20 rounded-2xl object-cover border-2 border-[var(--color-border)]"
            />
            <div className="flex-1">
              <h3 className="text-2xl font-bold text-[var(--color-text)]">
                {name}
              </h3>
              <p className="text-sm text-[var(--color-text-muted)]">
                {short_description || "—"}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-3 text-xs">
                <span
                  className="inline-block rounded-full px-3 py-1 font-bold uppercase"
                  style={{
                    backgroundColor:
                      review_status === "approved"
                        ? "rgba(16,185,129,0.2)"
                        : review_status === "pending"
                          ? "rgba(251,191,36,0.2)"
                          : review_status === "rejected"
                            ? "rgba(239,68,68,0.2)"
                            : "rgba(148,163,184,0.2)",
                    color:
                      review_status === "approved"
                        ? "#10b981"
                        : review_status === "pending"
                          ? "#f59e0b"
                          : review_status === "rejected"
                            ? "#ef4444"
                            : "#94a3b8",
                  }}
                >
                  {review_status || "unknown"}
                </span>
                {created_by?.display_name && (
                  <span className="text-[var(--color-text-muted)]">
                    by {created_by.display_name}
                  </span>
                )}
                {created_at && (
                  <span className="text-[var(--color-text-muted)]">
                    created {formatDate(created_at)}
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Action buttons */}
          {review_status === "pending" && (
            <div className="flex flex-wrap gap-3 mb-6">
              <button
                onClick={() => onApprove?.(character.character_id)}
                disabled={isProcessing}
                className="flex-1 min-w-[120px] rounded-xl bg-emerald-600 py-2.5 text-sm font-bold text-white shadow-lg shadow-emerald-900/20 transition hover:bg-emerald-500 disabled:opacity-50"
              >
                <CheckCircle size={18} className="inline mr-2" />
                Approve
              </button>
              <button
                onClick={() => setShowRejectInput(true)}
                className="flex-1 min-w-[120px] rounded-xl bg-rose-500/10 py-2.5 text-sm font-bold text-rose-500 border border-rose-500/20 transition hover:bg-rose-500/20"
              >
                <XCircle size={18} className="inline mr-2" />
                Reject
              </button>
            </div>
          )}

          {showRejectInput && (
            <div className="mb-6 p-4 rounded-xl bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-800">
              <textarea
                className="w-full rounded-lg border border-rose-300 dark:border-rose-700 p-3 text-sm bg-white dark:bg-slate-900 text-[var(--color-text)] outline-none focus:ring-2 focus:ring-rose-500"
                placeholder="Reason for rejection..."
                value={rejectionReason}
                onChange={(e) => setRejectionReason(e.target.value)}
                rows={3}
              />
              <div className="flex gap-2 mt-2">
                <button
                  onClick={() => {
                    onReject?.(character.character_id, rejectionReason);
                    setShowRejectInput(false);
                    setRejectionReason("");
                  }}
                  disabled={isProcessing || !rejectionReason.trim()}
                  className="flex-1 rounded-lg bg-rose-600 py-2 text-sm font-bold text-white disabled:opacity-50"
                >
                  Confirm Reject
                </button>
                <button
                  onClick={() => {
                    setShowRejectInput(false);
                    setRejectionReason("");
                  }}
                  className="flex-1 rounded-lg border border-[var(--color-border)] py-2 text-sm font-bold text-[var(--color-text-muted)]"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {existingRejectionReason && (
            <div className="mb-6 p-4 rounded-xl bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-800">
              <p className="text-xs font-bold uppercase text-rose-600 dark:text-rose-400">
                Previous Rejection Reason
              </p>
              <p className="text-sm mt-1 text-rose-700 dark:text-rose-300">
                {existingRejectionReason}
              </p>
            </div>
          )}

          {/* Details Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* LEFT COLUMN */}
            <div>
              <Section title="Basic Info">
                <DetailRow label="Name" value={name} icon={User} />
                <DetailRow
                  label="Short Description"
                  value={short_description}
                  icon={Info}
                />
                <DetailRow label="Mood" value={mood} icon={Sparkles} />
                <DetailRow label="Category" value={category} icon={BookOpen} />
                <DetailRow label="Intro" value={intro} icon={MessageSquare} />
                <DetailRow
                  label="Greeting Message"
                  value={greeting_message}
                  icon={MessageSquare}
                />
                <DetailRow
                  label="Tone Style"
                  value={tone_style}
                  icon={Sparkles}
                />
                <DetailRow
                  label="Primary Language"
                  value={primary_language}
                  icon={Globe}
                />
              </Section>

              {(country || town) && (
                <Section title="Location">
                  <DetailRow
                    label="Location"
                    value={[town, country].filter(Boolean).join(", ") || "—"}
                    icon={MapPin}
                  />
                </Section>
              )}

              {hasAppearance && (
                <Section title="Appearance">
                  {gender && (
                    <DetailRow
                      label="Gender"
                      value={genderDisplay}
                      icon={User}
                    />
                  )}
                  {age && (
                    <DetailRow
                      label="Age"
                      value={`${age} years`}
                      icon={Calendar}
                    />
                  )}
                  {visualStyleDisplay && (
                    <DetailRow
                      label="Art Style"
                      value={visualStyleDisplay}
                      icon={Palette}
                    />
                  )}
                  {skinColorDisplay && (
                    <DetailRow
                      label="Skin Tone"
                      value={skinColorDisplay}
                      icon={Palette}
                    />
                  )}
                  {eye_color && (
                    <DetailRow label="Eye Color" value={eye_color} icon={Eye} />
                  )}
                  {hairstyleDisplay && (
                    <DetailRow
                      label="Hairstyle"
                      value={hairstyleDisplay}
                      icon={Scissors}
                    />
                  )}
                  {hairColorDisplay && (
                    <DetailRow
                      label="Hair Color"
                      value={hairColorDisplay}
                      icon={Palette}
                    />
                  )}
                  {bodyTypeDisplay && (
                    <DetailRow
                      label="Body Type"
                      value={bodyTypeDisplay}
                      icon={Activity}
                    />
                  )}
                  {showFemaleAttrs &&
                    breastSizeDisplay &&
                    breastSizeDisplay !== "N/A" && (
                      <DetailRow
                        label="Breast Size"
                        value={breastSizeDisplay}
                        icon={Heart}
                      />
                    )}
                  {showFemaleAttrs &&
                    buttSizeDisplay &&
                    buttSizeDisplay !== "N/A" && (
                      <DetailRow
                        label="Butt Size"
                        value={buttSizeDisplay}
                        icon={Heart}
                      />
                    )}
                  {showMaleAttrs &&
                    chestHairDisplay &&
                    chestHairDisplay !== "N/A" && (
                      <DetailRow
                        label="Chest Hair"
                        value={chestHairDisplay}
                        icon={Activity}
                      />
                    )}
                  {showMaleAttrs &&
                    muscleDefinitionDisplay &&
                    muscleDefinitionDisplay !== "N/A" && (
                      <DetailRow
                        label="Muscle Definition"
                        value={muscleDefinitionDisplay}
                        icon={Activity}
                      />
                    )}
                  {showMaleAttrs &&
                    beardStyleDisplay &&
                    beardStyleDisplay !== "N/A" && (
                      <DetailRow
                        label="Beard Style"
                        value={beardStyleDisplay}
                        icon={User}
                      />
                    )}
                </Section>
              )}
            </div>

            {/* RIGHT COLUMN */}
            <div>
              <Section title="Personality & Lifestyle">
                {personalityNames && (
                  <DetailRow
                    label="Personality Traits"
                    value={personalityNames}
                    icon={Heart}
                  />
                )}
                {personality_description && (
                  <DetailRow
                    label="Personality Description"
                    value={personality_description}
                    icon={BookOpen}
                  />
                )}
                {relationshipStyleDisplay && (
                  <DetailRow
                    label="Relationship Style"
                    value={relationshipStyleDisplay}
                    icon={Heart}
                  />
                )}
                {clothingStyleDisplay && (
                  <DetailRow
                    label="Clothing Style"
                    value={clothingStyleDisplay}
                    icon={Image}
                  />
                )}
                {occupation?.name && (
                  <DetailRow
                    label="Occupation"
                    value={occupation.name}
                    icon={Briefcase}
                  />
                )}
                {hobbyNames && (
                  <DetailRow
                    label="Hobbies"
                    value={hobbyNames}
                    icon={Sparkles}
                  />
                )}
              </Section>

              {(system_instructions || prompt_template) && (
                <Section title="System & Prompt">
                  {system_instructions && (
                    <DetailRow
                      label="System Instructions"
                      value={system_instructions}
                      icon={Info}
                    />
                  )}
                  {prompt_template && (
                    <DetailRow
                      label="Prompt Template"
                      value={prompt_template}
                      icon={MessageSquare}
                    />
                  )}
                </Section>
              )}

              <Section title="Metadata">
                <DetailRow
                  label="Created At"
                  value={formatDate(created_at)}
                  icon={Calendar}
                />
                <DetailRow
                  label="Created By"
                  value={created_by?.display_name || "—"}
                  icon={User}
                />
              </Section>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
