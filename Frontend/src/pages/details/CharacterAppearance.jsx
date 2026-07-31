import { useLanguage } from "../../context/LanguageContext";
import { SectionTitle, AttributeRow } from "./shared";

export default function CharacterAppearance({
  gender,
  visualStyle,
  age,
  skinColor,
  eyeColor,
  hairLength,
  hairstyle,
  hairColor,
  bodyType,
  breastSize,
  buttSize,
  chestHair,
  muscleDefinition,
  beardStyle,
}) {
  const { t } = useLanguage();

  if (!gender && !age && !visualStyle) return null;

  const showFemaleAttrs = gender === "female";
  const showMaleAttrs = gender === "male";

  const formatChoice = (value, displayMap) => {
    if (!value) return "—";
    const mapped = displayMap[value];
    if (mapped) return typeof mapped === "function" ? mapped() : mapped;
    return value.replace(/_/g, " ");
  };

  const GENDER_DISPLAY = {
    male: () => t("wizard.genderMale"),
    female: () => t("wizard.genderFemale"),
  };

  const VISUAL_STYLE_DISPLAY = {
    realistic: () => t("wizard.styleRealistic"),
    anime: () => t("wizard.styleAnime"),
    cartoon: () => t("wizard.styleCartoon"),
  };

  const SKIN_COLOR_DISPLAY = {
    fair: () => t("wizard.skinFair"),
    light: () => t("wizard.skinLight"),
    medium: () => t("wizard.skinMedium"),
    olive: () => t("wizard.skinOlive"),
    tan: () => t("wizard.skinTan"),
    brown: () => t("wizard.skinBrown"),
    dark: () => t("wizard.skinDark"),
    ebony: () => t("wizard.skinEbony"),
  };

  const EYE_COLOR_DISPLAY = {
    brown: () => t("wizard.eyeBrown"),
    blue: () => t("wizard.eyeBlue"),
    green: () => t("wizard.eyeGreen"),
    hazel: () => t("wizard.eyeHazel"),
    gray: () => t("wizard.eyeGray"),
    amber: () => t("wizard.eyeAmber"),
    black: () => t("wizard.eyeBlack"),
  };

  const HAIR_LENGTH_DISPLAY = {
    bald: () => t("wizard.hairLengthBald"),
    short: () => t("wizard.hairLengthShort"),
    medium: () => t("wizard.hairLengthMedium"),
    long: () => t("wizard.hairLengthLong"),
  };

  const HAIRSTYLE_DISPLAY = {
    straight: () => t("wizard.hairStyleStraight"),
    wavy: () => t("wizard.hairStyleWavy"),
    curly: () => t("wizard.hairStyleCurly"),
    coily: () => t("wizard.hairStyleCurly"),
    braids: () => t("wizard.hairStyleBraids"),
    dreadlocks: () => t("wizard.hairStyleDreadlocks"),
    afro: () => t("wizard.hairStyleAfro"),
    ponytail: () => t("wizard.hairStylePonytail"),
    buzz_cut: () => t("wizard.hairStyleBuzzCut"),
    other: "Other",
  };

  const HAIR_COLOR_DISPLAY = {
    black: () => t("wizard.hairColorBlack"),
    brown: () => t("wizard.hairColorBrown"),
    blonde: () => t("wizard.hairColorBlonde"),
    red: () => t("wizard.hairColorRed"),
    gray: () => t("wizard.hairColorGray"),
    white: () => t("wizard.hairColorWhite"),
    blue: () => t("wizard.hairColorBlue"),
    pink: () => t("wizard.hairColorPink"),
    green: () => t("wizard.hairColorGreen"),
    purple: () => t("wizard.hairColorPurple"),
  };

  const BODY_TYPE_DISPLAY = {
    slim: () => t("wizard.bodySlim"),
    athletic: () => t("wizard.bodyAthletic"),
    average: () => t("wizard.bodyAverage"),
    curvy: () => t("wizard.bodyCurvy"),
    muscular: () => t("wizard.bodyMuscular"),
    chubby: () => t("wizard.bodyChubby"),
    petite: () => t("wizard.bodyPetite"),
    tall: () => t("wizard.bodyTall"),
  };

  const BREAST_SIZE_DISPLAY = {
    small: () => t("wizard.breastSmall"),
    medium: () => t("wizard.breastMedium"),
    large: () => t("wizard.breastLarge"),
    extra_large: () => t("wizard.breastExtraLarge"),
    na: () => t("wizard.na"),
  };

  const BUTT_SIZE_DISPLAY = {
    small: () => t("wizard.buttSmall"),
    medium: () => t("wizard.buttMedium"),
    large: () => t("wizard.buttLarge"),
    extra_large: () => t("wizard.buttExtraLarge"),
    na: () => t("wizard.na"),
  };

  const CHEST_HAIR_DISPLAY = {
    none: () => t("wizard.chestHairNone"),
    light: () => t("wizard.chestHairLight"),
    medium: () => t("wizard.chestHairMedium"),
    heavy: () => t("wizard.chestHairHeavy"),
    na: () => t("wizard.na"),
  };

  const MUSCLE_DEFINITION_DISPLAY = {
    slim: () => t("wizard.muscleSlim"),
    toned: () => t("wizard.muscleToned"),
    muscular: () => t("wizard.muscleMuscular"),
    bulky: () => t("wizard.muscleBulky"),
    na: () => t("wizard.na"),
  };

  const BEARD_STYLE_DISPLAY = {
    clean_shaven: () => t("wizard.beardCleanShaven"),
    stubble: () => t("wizard.beardStubble"),
    full_beard: () => t("wizard.beardFull"),
    goatee: () => t("wizard.beardGoatee"),
    mustache: () => t("wizard.beardMustache"),
    na: () => t("wizard.na"),
  };

  return (
    <>
      <SectionTitle title={t("characters.appearance")} />
      <div className="grid gap-4 sm:grid-cols-2">
        {/* General */}
        <div className="rounded-2xl border p-4 space-y-2 theme-card">
          <h5 className="text-sm font-semibold theme-text mb-2">
            {t("characters.general")}
          </h5>
          <AttributeRow
            label={t("wizard.gender")}
            value={formatChoice(gender, GENDER_DISPLAY)}
          />
          <AttributeRow
            label={t("wizard.visualStyle")}
            value={formatChoice(visualStyle, VISUAL_STYLE_DISPLAY)}
          />
          <AttributeRow
            label={t("wizard.age")}
            value={t("characters.ageWithYears", { age })}
          />
        </div>
        {/* Skin & Eyes */}
        <div className="rounded-2xl border p-4 space-y-2 theme-card">
          <h5 className="text-sm font-semibold theme-text mb-2">
            {t("characters.skinAndEyes")}
          </h5>
          <AttributeRow
            label={t("wizard.skinColor")}
            value={formatChoice(skinColor, SKIN_COLOR_DISPLAY)}
          />
          <AttributeRow
            label={t("wizard.eyeColor")}
            value={formatChoice(eyeColor, EYE_COLOR_DISPLAY)}
          />
        </div>
        {/* Hair */}
        <div className="rounded-2xl border p-4 space-y-2 theme-card">
          <h5 className="text-sm font-semibold theme-text mb-2">
            {t("characters.hair")}
          </h5>
          <AttributeRow
            label={t("wizard.hairLength")}
            value={formatChoice(hairLength, HAIR_LENGTH_DISPLAY)}
          />
          <AttributeRow
            label={t("wizard.hairstyle")}
            value={formatChoice(hairstyle, HAIRSTYLE_DISPLAY)}
          />
          <AttributeRow
            label={t("wizard.hairColor")}
            value={formatChoice(hairColor, HAIR_COLOR_DISPLAY)}
          />
        </div>
        {/* Body */}
        <div className="rounded-2xl border p-4 space-y-2 theme-card">
          <h5 className="text-sm font-semibold theme-text mb-2">
            {t("characters.body")}
          </h5>
          <AttributeRow
            label={t("wizard.bodyType")}
            value={formatChoice(bodyType, BODY_TYPE_DISPLAY)}
          />
          {showFemaleAttrs && breastSize && breastSize !== "na" && (
            <AttributeRow
              label={t("wizard.breastSize")}
              value={formatChoice(breastSize, BREAST_SIZE_DISPLAY)}
            />
          )}
          {showFemaleAttrs && buttSize && buttSize !== "na" && (
            <AttributeRow
              label={t("wizard.buttSize")}
              value={formatChoice(buttSize, BUTT_SIZE_DISPLAY)}
            />
          )}
          {showMaleAttrs && chestHair && chestHair !== "na" && (
            <AttributeRow
              label={t("wizard.chestHair")}
              value={formatChoice(chestHair, CHEST_HAIR_DISPLAY)}
            />
          )}
          {showMaleAttrs && muscleDefinition && muscleDefinition !== "na" && (
            <AttributeRow
              label={t("wizard.muscleDefinition")}
              value={formatChoice(muscleDefinition, MUSCLE_DEFINITION_DISPLAY)}
            />
          )}
          {showMaleAttrs && beardStyle && beardStyle !== "na" && (
            <AttributeRow
              label={t("wizard.beardStyle")}
              value={formatChoice(beardStyle, BEARD_STYLE_DISPLAY)}
            />
          )}
        </div>
      </div>
    </>
  );
}
