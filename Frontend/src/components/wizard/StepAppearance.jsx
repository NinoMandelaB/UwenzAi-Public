import { useWizard } from "../../context/WizardContext";
import { useLanguage } from "../../context/LanguageContext";
import PageCard from "../PageCard";

const SKIN_COLORS = [
  { value: "fair", labelKey: "wizard.skinFair", bg: "#fce4d6" },
  { value: "light", labelKey: "wizard.skinLight", bg: "#f5d0b8" },
  { value: "medium", labelKey: "wizard.skinMedium", bg: "#d4a373" },
  { value: "olive", labelKey: "wizard.skinOlive", bg: "#c4a484" },
  { value: "tan", labelKey: "wizard.skinTan", bg: "#b07d62" },
  { value: "brown", labelKey: "wizard.skinBrown", bg: "#8b5e3c" },
  { value: "dark", labelKey: "wizard.skinDark", bg: "#5c3a21" },
  { value: "ebony", labelKey: "wizard.skinEbony", bg: "#3b2210" },
];

const EYE_COLORS = [
  { value: "brown", labelKey: "wizard.eyeBrown", bg: "#5c3a21" },
  { value: "blue", labelKey: "wizard.eyeBlue", bg: "#3b82f6" },
  { value: "green", labelKey: "wizard.eyeGreen", bg: "#22c55e" },
  { value: "hazel", labelKey: "wizard.eyeHazel", bg: "#a0522d" },
  { value: "gray", labelKey: "wizard.eyeGray", bg: "#9ca3af" },
  { value: "amber", labelKey: "wizard.eyeAmber", bg: "#f59e0b" },
  { value: "black", labelKey: "wizard.eyeBlack", bg: "#1a1a1a" },
];

export default function StepAppearance() {
  const { state, updateState } = useWizard();
  const { t } = useLanguage();

  return (
    <PageCard className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold theme-text">
          {t("wizard.step2.title")}
        </h3>
        <p className="mt-1 text-sm theme-text-muted">
          {t("wizard.step2.subtitle")}
        </p>
      </div>

      {/* Age Slider */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <label className="text-sm font-medium theme-text">
            {t("wizard.age")}
          </label>
          <span
            className="rounded-full px-3 py-1 text-sm font-medium"
            style={{
              backgroundColor: "var(--color-accent-soft)",
              color: "var(--color-accent)",
            }}
          >
            {state.age} {t("wizard.years")}
          </span>
        </div>
        <input
          type="range"
          min="18"
          max="100"
          value={state.age}
          onChange={(e) => updateState({ age: parseInt(e.target.value, 10) })}
          className="w-full accent-cyan-400"
          style={{ accentColor: "var(--color-accent)" }}
        />
        <div className="flex justify-between text-xs theme-text-muted">
          <span>18</span>
          <span>100</span>
        </div>
      </div>

      {/* Skin Color */}
      <div className="space-y-3">
        <label className="text-sm font-medium theme-text">
          {t("wizard.skinColor")}
        </label>
        <div className="grid grid-cols-4 gap-3">
          {SKIN_COLORS.map((color) => (
            <button
              key={color.value}
              type="button"
              onClick={() => updateState({ skin_color: color.value })}
              className="flex flex-col items-center gap-2 rounded-xl border p-3 transition"
              style={{
                borderColor:
                  state.skin_color === color.value
                    ? "var(--color-accent)"
                    : "var(--color-border)",
                backgroundColor:
                  state.skin_color === color.value
                    ? "var(--color-accent-soft)"
                    : "var(--color-card)",
              }}
            >
              <div
                className="h-10 w-10 rounded-full border-2 border-white/20"
                style={{ backgroundColor: color.bg }}
              />
              <span className="text-xs theme-text">{t(color.labelKey)}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Eye Color */}
      <div className="space-y-3">
        <label className="text-sm font-medium theme-text">
          {t("wizard.eyeColor")}
        </label>
        <div className="grid grid-cols-4 gap-3 sm:grid-cols-7">
          {EYE_COLORS.map((color) => (
            <button
              key={color.value}
              type="button"
              onClick={() => updateState({ eye_color: color.value })}
              className="flex flex-col items-center gap-2 rounded-xl border p-3 transition"
              style={{
                borderColor:
                  state.eye_color === color.value
                    ? "var(--color-accent)"
                    : "var(--color-border)",
                backgroundColor:
                  state.eye_color === color.value
                    ? "var(--color-accent-soft)"
                    : "var(--color-card)",
              }}
            >
              <div
                className="h-10 w-10 rounded-full border-2 border-white/20"
                style={{ backgroundColor: color.bg }}
              />
              <span className="text-xs theme-text">{t(color.labelKey)}</span>
            </button>
          ))}
        </div>
      </div>
    </PageCard>
  );
}
