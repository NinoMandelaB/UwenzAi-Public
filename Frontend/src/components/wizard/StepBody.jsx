import { useEffect } from "react";
import { useWizard } from "../../context/WizardContext";
import { useLanguage } from "../../context/LanguageContext";
import PageCard from "../PageCard";

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

function OptionGroup({ label, options, value, onChange, t }) {
  return (
    <div>
      <label className="mb-3 block text-sm font-medium theme-text">
        {label}
      </label>
      <div className="flex flex-wrap gap-1.5">
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className="rounded-md px-2 py-1 text-[10px] font-medium transition"
            style={{
              backgroundColor:
                value === opt.value
                  ? "var(--color-accent)"
                  : "var(--color-soft)",
              color: value === opt.value ? "#082f49" : "var(--color-text)",
              border:
                value === opt.value
                  ? "1px solid transparent"
                  : "1px solid var(--color-border)",
            }}
          >
            {t(opt.labelKey)}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function StepBody() {
  const { state, updateState } = useWizard();
  const { t } = useLanguage();

  const gender = state.gender;

  useEffect(() => {
    if (gender === "male") {
      updateState({
        breast_size: "na",
        butt_size: "na",
      });
    }
    if (gender === "female") {
      updateState({
        chest_hair: "na",
        muscle_definition: "na",
        beard_style: "na",
      });
    }
  }, [gender, updateState]);

  const handleUpdate = (field, value) => {
    updateState({ [field]: value });
  };

  return (
    <PageCard className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold theme-text">
          {t("wizard.step4.title")}
        </h3>
        <p className="mt-1 text-sm theme-text-muted">
          {t("wizard.step4.subtitle")}
        </p>
      </div>

      <div className="space-y-10">
        <OptionGroup
          label={t("wizard.bodyType")}
          options={BODY_TYPES}
          value={state.body_type}
          onChange={(val) => handleUpdate("body_type", val)}
          t={t}
        />

        {(gender === "female" || gender === "non_binary") && (
          <>
            <OptionGroup
              label={t("wizard.breastSize")}
              options={BREAST_SIZES}
              value={state.breast_size}
              onChange={(val) => handleUpdate("breast_size", val)}
              t={t}
            />
            <OptionGroup
              label={t("wizard.buttSize")}
              options={BUTT_SIZES}
              value={state.butt_size}
              onChange={(val) => handleUpdate("butt_size", val)}
              t={t}
            />
          </>
        )}

        {(gender === "male" || gender === "non_binary") && (
          <>
            <OptionGroup
              label={t("wizard.chestHair")}
              options={CHEST_HAIR_OPTIONS}
              value={state.chest_hair}
              onChange={(val) => handleUpdate("chest_hair", val)}
              t={t}
            />
            <OptionGroup
              label={t("wizard.muscleDefinition")}
              options={MUSCLE_DEFINITIONS}
              value={state.muscle_definition}
              onChange={(val) => handleUpdate("muscle_definition", val)}
              t={t}
            />
            <OptionGroup
              label={t("wizard.beardStyle")}
              options={BEARD_STYLES}
              value={state.beard_style}
              onChange={(val) => handleUpdate("beard_style", val)}
              t={t}
            />
          </>
        )}
      </div>
    </PageCard>
  );
}
