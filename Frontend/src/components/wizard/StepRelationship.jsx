import { useWizard } from "../../context/WizardContext";
import { useLanguage } from "../../context/LanguageContext";
import PageCard from "../PageCard";
import {
  EVERYDAY_CLOTHING_STYLES,
  TRADITIONAL_CLOTHING_STYLES,
} from "../../config/clothingStyles";

const RELATIONSHIP_STYLES = [
  {
    value: "friend",
    labelKey: "wizard.relationshipFriend",
    emoji: "🤝",
    descriptionKey: "wizard.relationshipFriendDesc",
  },
  {
    value: "boyfriend",
    labelKey: "wizard.relationshipBoyfriend",
    emoji: "💙",
    descriptionKey: "wizard.relationshipBoyfriendDesc",
  },
  {
    value: "girlfriend",
    labelKey: "wizard.relationshipGirlfriend",
    emoji: "💗",
    descriptionKey: "wizard.relationshipGirlfriendDesc",
  },
  {
    value: "mentor",
    labelKey: "wizard.relationshipMentor",
    emoji: "🎓",
    descriptionKey: "wizard.relationshipMentorDesc",
  },
  {
    value: "companion",
    labelKey: "wizard.relationshipCompanion",
    emoji: "🌟",
    descriptionKey: "wizard.relationshipCompanionDesc",
  },
  {
    value: "flirty",
    labelKey: "wizard.relationshipFlirty",
    emoji: "😘",
    descriptionKey: "wizard.relationshipFlirtyDesc",
  },
  {
    value: "romantic",
    labelKey: "wizard.relationshipRomantic",
    emoji: "💖",
    descriptionKey: "wizard.relationshipRomanticDesc",
  },
];

function ClothingStyleGrid({ styles, selected, onSelect, t }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {styles.map((style) => {
        const isSelected = selected === style.value;
        return (
          <button
            key={style.value}
            type="button"
            onClick={() => onSelect(style.value)}
            className="flex flex-col items-center gap-2 rounded-xl border p-4 transition hover:border-cyan-400/50"
            style={{
              borderColor: isSelected
                ? "var(--color-accent)"
                : "var(--color-border)",
              backgroundColor: isSelected
                ? "var(--color-accent-soft)"
                : "var(--color-card)",
            }}
          >
            <span className="text-2xl">{style.emoji}</span>
            <span
              className="text-center text-sm font-medium"
              style={{
                color: isSelected ? "var(--color-accent)" : "var(--color-text)",
              }}
            >
              {t(style.labelKey)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default function StepRelationship() {
  const { state, updateState } = useWizard();
  const { t } = useLanguage();

  return (
    <PageCard className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold theme-text">
          {t("wizard.step7.title")}
        </h3>
        <p className="mt-1 text-sm theme-text-muted">
          {t("wizard.step7.subtitle")}
        </p>
      </div>

      {/* Relationship Style */}
      <div className="space-y-3">
        <label className="text-sm font-medium theme-text">
          {t("wizard.relationshipStyle")}
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {RELATIONSHIP_STYLES.map((style) => {
            const isSelected = state.relationship_style === style.value;
            return (
              <button
                key={style.value}
                type="button"
                onClick={() => updateState({ relationship_style: style.value })}
                className="flex items-start gap-3 rounded-xl border p-4 text-left transition hover:border-cyan-400/50"
                style={{
                  borderColor: isSelected
                    ? "var(--color-accent)"
                    : "var(--color-border)",
                  backgroundColor: isSelected
                    ? "var(--color-accent-soft)"
                    : "var(--color-card)",
                }}
              >
                <span className="text-2xl">{style.emoji}</span>
                <div className="flex-1">
                  <div className="flex items-center justify-between">
                    <span
                      className="font-medium"
                      style={{
                        color: isSelected
                          ? "var(--color-accent)"
                          : "var(--color-text)",
                      }}
                    >
                      {t(style.labelKey)}
                    </span>
                    {isSelected && (
                      <span
                        className="h-5 w-5 rounded-full"
                        style={{ backgroundColor: "var(--color-accent)" }}
                      >
                        <svg
                          className="h-5 w-5 text-white"
                          viewBox="0 0 20 20"
                          fill="currentColor"
                        >
                          <path
                            fillRule="evenodd"
                            d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                            clipRule="evenodd"
                          />
                        </svg>
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs theme-text-muted">
                    {t(style.descriptionKey)}
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Clothing Style — everyday styles, then traditional East African ones */}
      <div className="space-y-3">
        <label className="text-sm font-medium theme-text">
          {t("wizard.clothingStyle")}
        </label>
        <ClothingStyleGrid
          styles={EVERYDAY_CLOTHING_STYLES}
          selected={state.clothing_style}
          onSelect={(value) => updateState({ clothing_style: value })}
          t={t}
        />
        <p className="pt-2 text-xs font-medium uppercase tracking-wide theme-text-muted">
          {t("wizard.clothingGroupTraditional")}
        </p>
        <ClothingStyleGrid
          styles={TRADITIONAL_CLOTHING_STYLES}
          selected={state.clothing_style}
          onSelect={(value) => updateState({ clothing_style: value })}
          t={t}
        />
      </div>
    </PageCard>
  );
}
