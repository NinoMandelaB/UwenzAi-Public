import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../../context/WizardContext";
import { useLanguage } from "../../context/LanguageContext";
import { useAuth } from "../../context/AuthContext";
import { createCharacterFromWizard } from "../../services/characterService";
import { clearOperationId, reuseOperationId } from "../../utils/idempotency";
import { getCharacterMessage } from "../../utils/characterMessageMapper";
import PageCard from "../PageCard";
import { Loader2, CheckCircle, AlertCircle } from "lucide-react";

// Helper to get label from value using options array
function getLabelFromValue(options, value, t, fallback = "") {
  const option = options.find((opt) => opt.value === value);
  return option?.labelKey
    ? t(option.labelKey)
    : option?.label || fallback || value;
}

// Options for display (imported from previous steps for consistency)
const GENDER_OPTIONS = [
  { value: "female", labelKey: "wizard.genderFemale" },
  { value: "male", labelKey: "wizard.genderMale" },
  { value: "non_binary", labelKey: "wizard.genderNonBinary" },
  { value: "other", labelKey: "wizard.genderOther" },
];

const STYLE_OPTIONS = [
  { value: "realistic", labelKey: "wizard.styleRealistic" },
  { value: "anime", labelKey: "wizard.styleAnime" },
  { value: "cartoon", labelKey: "wizard.styleCartoon" },
];

const SKIN_COLORS = [
  { value: "fair", labelKey: "wizard.skinFair" },
  { value: "light", labelKey: "wizard.skinLight" },
  { value: "medium", labelKey: "wizard.skinMedium" },
  { value: "olive", labelKey: "wizard.skinOlive" },
  { value: "tan", labelKey: "wizard.skinTan" },
  { value: "brown", labelKey: "wizard.skinBrown" },
  { value: "dark", labelKey: "wizard.skinDark" },
  { value: "ebony", labelKey: "wizard.skinEbony" },
];

const HAIRSTYLES = [
  { value: "short", labelKey: "wizard.hairLengthShort" },
  { value: "medium", labelKey: "wizard.hairLengthMedium" },
  { value: "long", labelKey: "wizard.hairLengthLong" },
  { value: "curly", labelKey: "wizard.hairStyleCurly" },
  { value: "wavy", labelKey: "wizard.hairStyleWavy" },
  { value: "straight", labelKey: "wizard.hairStyleStraight" },
  { value: "bald", labelKey: "wizard.hairLengthBald" },
  { value: "buzz_cut", labelKey: "wizard.hairStyleBuzzCut" },
  { value: "ponytail", labelKey: "wizard.hairStylePonytail" },
  { value: "braids", labelKey: "wizard.hairStyleBraids" },
  { value: "dreadlocks", labelKey: "wizard.hairStyleDreadlocks" },
  { value: "afro", labelKey: "wizard.hairStyleAfro" },
];

const HAIR_COLORS = [
  { value: "black", labelKey: "wizard.hairColorBlack" },
  { value: "brown", labelKey: "wizard.hairColorBrown" },
  { value: "blonde", labelKey: "wizard.hairColorBlonde" },
  { value: "red", labelKey: "wizard.hairColorRed" },
  { value: "gray", labelKey: "wizard.hairColorGray" },
  { value: "white", labelKey: "wizard.hairColorWhite" },
  { value: "blue", labelKey: "wizard.hairColorBlue" },
  { value: "pink", labelKey: "wizard.hairColorPink" },
  { value: "green", labelKey: "wizard.hairColorGreen" },
  { value: "purple", labelKey: "wizard.hairColorPurple" },
];

const BODY_TYPES = [
  { value: "slim", labelKey: "wizard.bodySlim" },
  { value: "athletic", labelKey: "wizard.bodyAthletic" },
  { value: "average", labelKey: "wizard.bodyAverage" },
  { value: "curvy", labelKey: "wizard.bodyCurvy" },
  { value: "muscular", labelKey: "wizard.bodyMuscular" },
  { value: "chubby", labelKey: "wizard.bodyChubby" },
  { value: "petite", labelKey: "wizard.bodyPetite" },
  { value: "tall", labelKey: "wizard.bodyTall" },
];

const BREAST_SIZES = [
  { value: "small", labelKey: "wizard.breastSmall" },
  { value: "medium", labelKey: "wizard.breastMedium" },
  { value: "large", labelKey: "wizard.breastLarge" },
  { value: "extra_large", labelKey: "wizard.breastExtraLarge" },
  { value: "na", labelKey: "wizard.na" },
];

const BUTT_SIZES = [
  { value: "small", labelKey: "wizard.buttSmall" },
  { value: "medium", labelKey: "wizard.buttMedium" },
  { value: "large", labelKey: "wizard.buttLarge" },
  { value: "extra_large", labelKey: "wizard.buttExtraLarge" },
  { value: "na", labelKey: "wizard.na" },
];

const CHEST_HAIR_OPTIONS = [
  { value: "none", labelKey: "wizard.chestHairNone" },
  { value: "light", labelKey: "wizard.chestHairLight" },
  { value: "medium", labelKey: "wizard.chestHairMedium" },
  { value: "heavy", labelKey: "wizard.chestHairHeavy" },
  { value: "na", labelKey: "wizard.na" },
];

const MUSCLE_DEFINITIONS = [
  { value: "slim", labelKey: "wizard.muscleSlim" },
  { value: "toned", labelKey: "wizard.muscleToned" },
  { value: "muscular", labelKey: "wizard.muscleMuscular" },
  { value: "bulky", labelKey: "wizard.muscleBulky" },
  { value: "na", labelKey: "wizard.na" },
];

const BEARD_STYLES = [
  { value: "clean_shaven", labelKey: "wizard.beardCleanShaven" },
  { value: "stubble", labelKey: "wizard.beardStubble" },
  { value: "full_beard", labelKey: "wizard.beardFull" },
  { value: "goatee", labelKey: "wizard.beardGoatee" },
  { value: "mustache", labelKey: "wizard.beardMustache" },
  { value: "na", labelKey: "wizard.na" },
];

const PERSONALITY_TRAITS = [
  { value: "witty", labelKey: "wizard.personalityWitty" },
  { value: "empathetic", labelKey: "wizard.personalityEmpathetic" },
  { value: "adventurous", labelKey: "wizard.personalityAdventurous" },
  { value: "shy", labelKey: "wizard.personalityShy" },
  { value: "confident", labelKey: "wizard.personalityConfident" },
  { value: "romantic", labelKey: "wizard.personalityRomantic" },
  { value: "funny", labelKey: "wizard.personalityFunny" },
  { value: "intellectual", labelKey: "wizard.personalityIntellectual" },
  { value: "caring", labelKey: "wizard.personalityCaring" },
  { value: "mysterious", labelKey: "wizard.personalityMysterious" },
  { value: "friendly", labelKey: "wizard.personalityFriendly" },
];

const RELATIONSHIP_STYLES = [
  { value: "friend", labelKey: "wizard.relationshipFriend" },
  { value: "boyfriend", labelKey: "wizard.relationshipBoyfriend" },
  { value: "girlfriend", labelKey: "wizard.relationshipGirlfriend" },
  { value: "mentor", labelKey: "wizard.relationshipMentor" },
  { value: "companion", labelKey: "wizard.relationshipCompanion" },
  { value: "flirty", labelKey: "wizard.relationshipFlirty" },
  { value: "romantic", labelKey: "wizard.relationshipRomantic" },
];

const CLOTHING_STYLES = [
  { value: "casual", labelKey: "wizard.clothingCasual" },
  { value: "formal", labelKey: "wizard.clothingFormal" },
  { value: "sporty", labelKey: "wizard.clothingSporty" },
  { value: "elegant", labelKey: "wizard.clothingElegant" },
  { value: "streetwear", labelKey: "wizard.clothingStreetwear" },
  { value: "bohemian", labelKey: "wizard.clothingBohemian" },
  { value: "gothic", labelKey: "wizard.clothingGothic" },
  { value: "preppy", labelKey: "wizard.clothingPreppy" },
];

function SummaryRow({ label, value }) {
  return (
    <div
      className="flex items-baseline justify-between border-b py-2 text-sm"
      style={{ borderColor: "var(--color-border)" }}
    >
      <span className="theme-text-muted">{label}</span>
      <span className="font-medium theme-text">{value || "—"}</span>
    </div>
  );
}

function SummarySection({ title, children }) {
  return (
    <div className="space-y-2">
      <h4 className="text-base font-semibold theme-text">{title}</h4>
      <div
        className="rounded-xl border p-4"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-soft)",
        }}
      >
        {children}
      </div>
    </div>
  );
}

export default function StepReview() {
  const navigate = useNavigate();
  const { state, clearDraft, setCreatedCharacterId, goToStep } = useWizard();
  const { t } = useLanguage();
  useAuth();

  const [isSubmitting, setIsSubmitting] = useState(false);
  // Held across retries so a resubmitted wizard returns the character the
  // first attempt created rather than paying to generate a second persona.
  const createOperationRef = useRef(null);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async () => {
    if (isSubmitting) return;

    setIsSubmitting(true);
    setError(null);

    try {
      const result = await createCharacterFromWizard(
        state,
        reuseOperationId(createOperationRef, state.name || ""),
      );
      clearOperationId(createOperationRef);

      setSuccess(true);
      setCreatedCharacterId(result?.data?.character?.character_id);

      clearDraft();

      const characterId = result?.data?.character?.character_id;
      if (characterId) {
        setTimeout(() => {
          navigate(`/characters/${characterId}/setup`);
        }, 1500);
      } else {
        setTimeout(() => {
          navigate("/characters");
        }, 2000);
      }
    } catch (err) {
      console.error("Failed to create character:", err);
      setError(getCharacterMessage(err, t("wizard.createError")));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleEditStep = (stepId) => {
    goToStep(stepId);
  };

  if (success) {
    return (
      <PageCard className="text-center">
        <div className="flex flex-col items-center gap-4 py-8">
          <div className="rounded-full bg-emerald-500/20 p-3">
            <CheckCircle className="h-12 w-12 text-emerald-400" />
          </div>
          <h3 className="text-xl font-semibold theme-text">
            {t("wizard.successTitle")}
          </h3>
          <p className="text-sm theme-text-muted">
            {t("wizard.successMessage")}
          </p>
        </div>
      </PageCard>
    );
  }

  return (
    <PageCard className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold theme-text">
          {t("wizard.step8.title")}
        </h3>
        <p className="mt-1 text-sm theme-text-muted">
          {t("wizard.step8.subtitle")}
        </p>
      </div>

      {/* Error Display */}
      {error && (
        <div className="flex items-start gap-3 rounded-xl border border-red-500/30 bg-red-500/10 p-4">
          <AlertCircle className="h-5 w-5 shrink-0 text-red-400" />
          <p className="text-sm text-red-300">{error}</p>
        </div>
      )}

      <div className="space-y-4">
        {/* Step 1: Basic Info */}
        <SummarySection title={t("wizard.step1.title")}>
          <div className="space-y-1">
            <SummaryRow label={t("wizard.name")} value={state.name || "—"} />
            <SummaryRow
              label={t("wizard.gender")}
              value={getLabelFromValue(GENDER_OPTIONS, state.gender, t)}
            />
            <SummaryRow
              label={t("wizard.visualStyle")}
              value={getLabelFromValue(STYLE_OPTIONS, state.visual_style, t)}
            />
            <SummaryRow
              label={t("wizard.country")}
              value={state.country || "—"}
            />
            <SummaryRow label={t("wizard.town")} value={state.town || "—"} />
            <SummaryRow
              label={t("wizard.visibility")}
              value={
                state.is_public === true
                  ? t("wizard.public")
                  : state.is_public === false
                    ? t("wizard.private")
                    : "—"
              }
            />
          </div>
          <button
            type="button"
            onClick={() => handleEditStep(1)}
            className="mt-3 text-xs font-medium"
            style={{ color: "var(--color-accent)" }}
          >
            {t("wizard.edit")}
          </button>
        </SummarySection>

        {/* Step 2: Appearance */}
        <SummarySection title={t("wizard.step2.title")}>
          <div className="space-y-1">
            <SummaryRow
              label={t("wizard.age")}
              value={`${state.age} ${t("wizard.years")}`}
            />
            <SummaryRow
              label={t("wizard.skinColor")}
              value={getLabelFromValue(SKIN_COLORS, state.skin_color, t)}
            />
            <SummaryRow
              label={t("wizard.eyeColor")}
              value={
                state.eye_color
                  ? t(
                      `wizard.eye${state.eye_color.charAt(0).toUpperCase() + state.eye_color.slice(1)}`,
                    )
                  : "—"
              }
            />
          </div>
          <button
            type="button"
            onClick={() => handleEditStep(2)}
            className="mt-3 text-xs font-medium"
            style={{ color: "var(--color-accent)" }}
          >
            {t("wizard.edit")}
          </button>
        </SummarySection>

        {/* Step 3: Hair */}
        <SummarySection title={t("wizard.step3.title")}>
          <div className="space-y-1">
            <SummaryRow
              label={t("wizard.hairstyle")}
              value={getLabelFromValue(HAIRSTYLES, state.hairstyle, t)}
            />
            <SummaryRow
              label={t("wizard.hairColor")}
              value={getLabelFromValue(HAIR_COLORS, state.hair_color, t)}
            />
          </div>
          <button
            type="button"
            onClick={() => handleEditStep(3)}
            className="mt-3 text-xs font-medium"
            style={{ color: "var(--color-accent)" }}
          >
            {t("wizard.edit")}
          </button>
        </SummarySection>

        {/* Step 4: Body */}
        <SummarySection title={t("wizard.step4.title")}>
          <div className="space-y-1">
            <SummaryRow
              label={t("wizard.bodyType")}
              value={getLabelFromValue(BODY_TYPES, state.body_type, t)}
            />
            {(state.gender === "female" || state.gender === "non_binary") && (
              <>
                <SummaryRow
                  label={t("wizard.breastSize")}
                  value={getLabelFromValue(BREAST_SIZES, state.breast_size, t)}
                />
                <SummaryRow
                  label={t("wizard.buttSize")}
                  value={getLabelFromValue(BUTT_SIZES, state.butt_size, t)}
                />
              </>
            )}
            {(state.gender === "male" || state.gender === "non_binary") && (
              <>
                <SummaryRow
                  label={t("wizard.chestHair")}
                  value={getLabelFromValue(
                    CHEST_HAIR_OPTIONS,
                    state.chest_hair,
                    t,
                  )}
                />
                <SummaryRow
                  label={t("wizard.muscleDefinition")}
                  value={getLabelFromValue(
                    MUSCLE_DEFINITIONS,
                    state.muscle_definition,
                    t,
                  )}
                />
                <SummaryRow
                  label={t("wizard.beardStyle")}
                  value={getLabelFromValue(BEARD_STYLES, state.beard_style, t)}
                />
              </>
            )}
          </div>
          <button
            type="button"
            onClick={() => handleEditStep(4)}
            className="mt-3 text-xs font-medium"
            style={{ color: "var(--color-accent)" }}
          >
            {t("wizard.edit")}
          </button>
        </SummarySection>

        {/* Step 5: Personality */}
        <SummarySection title={t("wizard.step5.title")}>
          <div className="space-y-1">
            <SummaryRow
              label={t("wizard.personality")}
              value={
                state.personality
                  ? getLabelFromValue(PERSONALITY_TRAITS, state.personality, t)
                  : "—"
              }
            />
          </div>
          <button
            type="button"
            onClick={() => handleEditStep(5)}
            className="mt-3 text-xs font-medium"
            style={{ color: "var(--color-accent)" }}
          >
            {t("wizard.edit")}
          </button>
        </SummarySection>

        {/* Step 6: Lifestyle */}
        <SummarySection title={t("wizard.step6.title")}>
          <div className="space-y-1">
            <SummaryRow
              label={t("wizard.occupation")}
              value={state.occupation_id ? t("wizard.selected") : "—"}
            />
            <SummaryRow
              label={t("wizard.hobbies")}
              value={`${(state.hobby_ids || []).length} ${t("wizard.selected")}`}
            />
          </div>
          <button
            type="button"
            onClick={() => handleEditStep(6)}
            className="mt-3 text-xs font-medium"
            style={{ color: "var(--color-accent)" }}
          >
            {t("wizard.edit")}
          </button>
        </SummarySection>

        {/* Step 7: Relationship */}
        <SummarySection title={t("wizard.step7.title")}>
          <div className="space-y-1">
            <SummaryRow
              label={t("wizard.relationshipStyle")}
              value={getLabelFromValue(
                RELATIONSHIP_STYLES,
                state.relationship_style,
                t,
              )}
            />
            <SummaryRow
              label={t("wizard.clothingStyle")}
              value={getLabelFromValue(
                CLOTHING_STYLES,
                state.clothing_style,
                t,
              )}
            />
          </div>
          <button
            type="button"
            onClick={() => handleEditStep(7)}
            className="mt-3 text-xs font-medium"
            style={{ color: "var(--color-accent)" }}
          >
            {t("wizard.edit")}
          </button>
        </SummarySection>
      </div>

      {/* Submit Button */}
      <div
        className="flex justify-end border-t pt-4"
        style={{ borderColor: "var(--color-border)" }}
      >
        <button
          type="button"
          onClick={handleSubmit}
          disabled={isSubmitting}
          className="flex items-center gap-2 rounded-xl px-6 py-3 text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
          style={{
            backgroundColor: "var(--color-accent)",
            color: "#082f49",
          }}
        >
          {isSubmitting ? (
            <>
              <Loader2 className="animate-spin" size={16} />
              {t("wizard.creating")}
            </>
          ) : (
            t("wizard.createCharacter")
          )}
        </button>
      </div>
    </PageCard>
  );
}
