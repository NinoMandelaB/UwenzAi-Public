import { useWizard } from "../../context/WizardContext";
import { useLanguage } from "../../context/LanguageContext";
import PageCard from "../PageCard";

// --- FEMALE IMPORTS ---
import f_anime_03 from "../../assets/images/wizard/female/hairstyle/anime/03_long_straight.jpg";
import f_anime_04 from "../../assets/images/wizard/female/hairstyle/anime/04_long_wavy.jpg";
import f_anime_05 from "../../assets/images/wizard/female/hairstyle/anime/05_curly.jpg";
import f_anime_06 from "../../assets/images/wizard/female/hairstyle/anime/06_afro.jpg";
import f_anime_07 from "../../assets/images/wizard/female/hairstyle/anime/07_braids.jpg";
import f_anime_08 from "../../assets/images/wizard/female/hairstyle/anime/08_dreadlocks.jpg";
import f_anime_09 from "../../assets/images/wizard/female/hairstyle/anime/09_ponytail.jpg";
import f_anime_10 from "../../assets/images/wizard/female/hairstyle/anime/10_bun.jpg";
import f_anime_11 from "../../assets/images/wizard/female/hairstyle/anime/11_bangs.jpg";
import f_anime_12 from "../../assets/images/wizard/female/hairstyle/anime/12_shaved.jpg";

import f_cartoon_03 from "../../assets/images/wizard/female/hairstyle/cartoon/03_long_straight.jpg";
import f_cartoon_04 from "../../assets/images/wizard/female/hairstyle/cartoon/04_long_wavy.jpg";
import f_cartoon_05 from "../../assets/images/wizard/female/hairstyle/cartoon/05_curly.jpg";
import f_cartoon_06 from "../../assets/images/wizard/female/hairstyle/cartoon/06_afro.jpg";
import f_cartoon_07 from "../../assets/images/wizard/female/hairstyle/cartoon/07_braids.jpg";
import f_cartoon_08 from "../../assets/images/wizard/female/hairstyle/cartoon/08_dreadlocks.jpg";
import f_cartoon_09 from "../../assets/images/wizard/female/hairstyle/cartoon/09_ponytail.jpg";
import f_cartoon_10 from "../../assets/images/wizard/female/hairstyle/cartoon/10_bun.jpg";
import f_cartoon_11 from "../../assets/images/wizard/female/hairstyle/cartoon/11_bangs.jpg";
import f_cartoon_12 from "../../assets/images/wizard/female/hairstyle/cartoon/12_shaved.jpg";

import f_realistic_03 from "../../assets/images/wizard/female/hairstyle/realistic/03_long_straight.jpg";
import f_realistic_04 from "../../assets/images/wizard/female/hairstyle/realistic/04_long_wavy.jpg";
import f_realistic_05 from "../../assets/images/wizard/female/hairstyle/realistic/05_curly.jpg";
import f_realistic_06 from "../../assets/images/wizard/female/hairstyle/realistic/06_afro.jpg";
import f_realistic_07 from "../../assets/images/wizard/female/hairstyle/realistic/07_braids.jpg";
import f_realistic_08 from "../../assets/images/wizard/female/hairstyle/realistic/08_dreadlocks.jpg";
import f_realistic_09 from "../../assets/images/wizard/female/hairstyle/realistic/09_ponytail.jpg";
import f_realistic_10 from "../../assets/images/wizard/female/hairstyle/realistic/10_bun.jpg";
import f_realistic_11 from "../../assets/images/wizard/female/hairstyle/realistic/11_bangs.jpg";
import f_realistic_12 from "../../assets/images/wizard/female/hairstyle/realistic/12_shaved.jpg";

// --- MALE IMPORTS ---
import m_anime_03 from "../../assets/images/wizard/male/hairstyle/anime/03_short_afro.jpg";
import m_anime_04 from "../../assets/images/wizard/male/hairstyle/anime/04_medium_curly.jpg";
import m_anime_05 from "../../assets/images/wizard/male/hairstyle/anime/05_long_locs.jpg";
import m_anime_06 from "../../assets/images/wizard/male/hairstyle/anime/06_afro.jpg";
import m_anime_07 from "../../assets/images/wizard/male/hairstyle/anime/07_cornrows.jpg";
import m_anime_08 from "../../assets/images/wizard/male/hairstyle/anime/08_dreadlocks.jpg";
import m_anime_09 from "../../assets/images/wizard/male/hairstyle/anime/09_bald.jpg";
import m_anime_10 from "../../assets/images/wizard/male/hairstyle/anime/10_man_bun.jpg";
import m_anime_11 from "../../assets/images/wizard/male/hairstyle/anime/11_undercut.jpg";
import m_anime_12 from "../../assets/images/wizard/male/hairstyle/anime/12_waves.jpg";

import m_cartoon_03 from "../../assets/images/wizard/male/hairstyle/cartoon/03_short_afro.jpg";
import m_cartoon_04 from "../../assets/images/wizard/male/hairstyle/cartoon/04_medium_curly.jpg";
import m_cartoon_05 from "../../assets/images/wizard/male/hairstyle/cartoon/05_long_locs.jpg";
import m_cartoon_06 from "../../assets/images/wizard/male/hairstyle/cartoon/06_afro.jpg";
import m_cartoon_07 from "../../assets/images/wizard/male/hairstyle/cartoon/07_cornrows.jpg";
import m_cartoon_08 from "../../assets/images/wizard/male/hairstyle/cartoon/08_dreadlocks.jpg";
import m_cartoon_09 from "../../assets/images/wizard/male/hairstyle/cartoon/09_bald.jpg";
import m_cartoon_10 from "../../assets/images/wizard/male/hairstyle/cartoon/10_man_bun.jpg";
import m_cartoon_11 from "../../assets/images/wizard/male/hairstyle/cartoon/11_undercut.jpg";
import m_cartoon_12 from "../../assets/images/wizard/male/hairstyle/cartoon/12_waves.jpg";

import m_realistic_03 from "../../assets/images/wizard/male/hairstyle/realistic/03_short_afro.jpg";
import m_realistic_04 from "../../assets/images/wizard/male/hairstyle/realistic/04_medium_curly.jpg";
import m_realistic_05 from "../../assets/images/wizard/male/hairstyle/realistic/05_long_locs.jpg";
import m_realistic_06 from "../../assets/images/wizard/male/hairstyle/realistic/06_afro.jpg";
import m_realistic_07 from "../../assets/images/wizard/male/hairstyle/realistic/07_cornrows.jpg";
import m_realistic_08 from "../../assets/images/wizard/male/hairstyle/realistic/08_dreadlocks.jpg";
import m_realistic_09 from "../../assets/images/wizard/male/hairstyle/realistic/09_bald.jpg";
import m_realistic_10 from "../../assets/images/wizard/male/hairstyle/realistic/10_man_bun.jpg";
import m_realistic_11 from "../../assets/images/wizard/male/hairstyle/realistic/11_undercut.jpg";
import m_realistic_12 from "../../assets/images/wizard/male/hairstyle/realistic/12_waves.jpg";

const IMAGE_MAP = {
  female: {
    anime: {
      straight: f_anime_03,
      wavy: f_anime_04,
      curly: f_anime_05,
      afro: f_anime_06,
      braids: f_anime_07,
      dreadlocks: f_anime_08,
      ponytail: f_anime_09,
      bun: f_anime_10,
      bangs: f_anime_11,
      shaved: f_anime_12,
    },
    cartoon: {
      straight: f_cartoon_03,
      wavy: f_cartoon_04,
      curly: f_cartoon_05,
      afro: f_cartoon_06,
      braids: f_cartoon_07,
      dreadlocks: f_cartoon_08,
      ponytail: f_cartoon_09,
      bun: f_cartoon_10,
      bangs: f_cartoon_11,
      shaved: f_cartoon_12,
    },
    realistic: {
      straight: f_realistic_03,
      wavy: f_realistic_04,
      curly: f_realistic_05,
      afro: f_realistic_06,
      braids: f_realistic_07,
      dreadlocks: f_realistic_08,
      ponytail: f_realistic_09,
      bun: f_realistic_10,
      bangs: f_realistic_11,
      shaved: f_realistic_12,
    },
  },
  male: {
    anime: {
      straight: m_anime_03,
      wavy: m_anime_04,
      curly: m_anime_05,
      afro: m_anime_06,
      braids: m_anime_07,
      dreadlocks: m_anime_08,
      ponytail: m_anime_09,
      bun: m_anime_10,
      bangs: m_anime_11,
      shaved: m_anime_12,
    },
    cartoon: {
      straight: m_cartoon_03,
      wavy: m_cartoon_04,
      curly: m_cartoon_05,
      afro: m_cartoon_06,
      braids: m_cartoon_07,
      dreadlocks: m_cartoon_08,
      ponytail: m_cartoon_09,
      bun: m_cartoon_10,
      bangs: m_cartoon_11,
      shaved: m_cartoon_12,
    },
    realistic: {
      straight: m_realistic_03,
      wavy: m_realistic_04,
      curly: m_realistic_05,
      afro: m_realistic_06,
      braids: m_realistic_07,
      dreadlocks: m_realistic_08,
      ponytail: m_realistic_09,
      bun: m_realistic_10,
      bangs: m_realistic_11,
      shaved: m_realistic_12,
    },
  },
};

const HAIR_LENGTHS = [
  { value: "bald", labelKey: "wizard.hairLengthBald" },
  { value: "short", labelKey: "wizard.hairLengthShort" },
  { value: "medium", labelKey: "wizard.hairLengthMedium" },
  { value: "long", labelKey: "wizard.hairLengthLong" },
];

const HAIR_STYLES = [
  { value: "straight", labelKey: "wizard.hairStyleStraight" },
  { value: "wavy", labelKey: "wizard.hairStyleWavy" },
  { value: "curly", labelKey: "wizard.hairStyleCurly" },
  { value: "afro", labelKey: "wizard.hairStyleAfro" },
  { value: "braids", labelKey: "wizard.hairStyleBraids" },
  { value: "dreadlocks", labelKey: "wizard.hairStyleDreadlocks" },
  { value: "ponytail", labelKey: "wizard.hairStylePonytail" },
  { value: "bun", labelKey: "wizard.hairStyleBun" },
  { value: "bangs", labelKey: "wizard.hairStyleBangs" },
  { value: "shaved", labelKey: "wizard.hairStyleShaved" },
];

const HAIR_COLORS = [
  { value: "black", labelKey: "wizard.hairColorBlack", bg: "#1a1a1a" },
  { value: "brown", labelKey: "wizard.hairColorBrown", bg: "#5c3a21" },
  { value: "blonde", labelKey: "wizard.hairColorBlonde", bg: "#e5b97a" },
  { value: "red", labelKey: "wizard.hairColorRed", bg: "#b02c2c" },
  { value: "gray", labelKey: "wizard.hairColorGray", bg: "#9ca3af" },
  { value: "white", labelKey: "wizard.hairColorWhite", bg: "#f3f4f6" },
  { value: "blue", labelKey: "wizard.hairColorBlue", bg: "#3b82f6" },
  { value: "pink", labelKey: "wizard.hairColorPink", bg: "#ec4899" },
  { value: "green", labelKey: "wizard.hairColorGreen", bg: "#22c55e" },
  { value: "purple", labelKey: "wizard.hairColorPurple", bg: "#a855f7" },
];

export default function StepHair() {
  const { state, updateState } = useWizard();
  const { t } = useLanguage();

  const gender = state.gender || "female";
  const artstyle = state.visual_style || "anime";

  return (
    <PageCard className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold theme-text">
          {t("wizard.step3.title")}
        </h3>
      </div>

      <div className="space-y-3">
        <label className="text-sm font-medium theme-text">
          {t("wizard.hairLength")}
        </label>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {HAIR_LENGTHS.map((length) => (
            <button
              key={length.value}
              type="button"
              onClick={() => updateState({ hair_length: length.value })}
              className="rounded-md px-3 py-1.5 text-xs font-medium transition"
              style={{
                backgroundColor:
                  state.hair_length === length.value
                    ? "var(--color-accent)"
                    : "var(--color-soft)",
                color:
                  state.hair_length === length.value
                    ? "#082f49"
                    : "var(--color-text)",
                border:
                  state.hair_length === length.value
                    ? "1px solid transparent"
                    : "1px solid var(--color-border)",
              }}
            >
              {t(length.labelKey)}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-3">
        <label className="text-sm font-medium theme-text">
          {t("wizard.hairStyle")}
        </label>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {HAIR_STYLES.map((style) => {
            const image = IMAGE_MAP[gender]?.[artstyle]?.[style.value];
            const isSelected = state.hairstyle === style.value;

            return (
              <button
                key={style.value}
                type="button"
                onClick={() => updateState({ hairstyle: style.value })}
                className="flex flex-col items-center p-2 rounded-xl transition border-2"
                style={{
                  borderColor: isSelected
                    ? "var(--color-accent)"
                    : "var(--color-border)",
                  backgroundColor: isSelected
                    ? "var(--color-soft)"
                    : "transparent",
                }}
              >
                {image && (
                  <div className="w-full aspect-square flex items-center justify-center overflow-hidden rounded-lg mb-2 bg-gray-50/50">
                    <img
                      src={image}
                      alt={t(style.labelKey)}
                      className="max-w-full max-h-full object-contain rounded-lg"
                    />
                  </div>
                )}
                <span className="text-xs font-medium theme-text">
                  {t(style.labelKey)}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="space-y-3">
        <label className="text-sm font-medium theme-text">
          {t("wizard.hairColor")}
        </label>
        <div className="grid grid-cols-5 gap-2">
          {HAIR_COLORS.map((color) => (
            <button
              key={color.value}
              type="button"
              onClick={() => updateState({ hair_color: color.value })}
              className="flex flex-col items-center gap-1"
            >
              <div
                className="h-10 w-10 rounded-full border-2 transition"
                style={{
                  backgroundColor: color.bg,
                  borderColor:
                    state.hair_color === color.value
                      ? "var(--color-accent)"
                      : "transparent",
                }}
              />
              <span className="text-[10px] theme-text-muted">
                {t(color.labelKey)}
              </span>
            </button>
          ))}
        </div>
      </div>
    </PageCard>
  );
}
