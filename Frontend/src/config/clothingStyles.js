// src/config/clothingStyles.js
//
// Single source of truth for the character "clothing style" choices.
//
// The wizard (step 7), the review step, the edit form and the character detail
// views all used to keep their own copy of this list, which is how "Lingerie /
// Underwear" ended up selectable on the edit tab but missing from the creation
// wizard. Add or rename a style HERE and every surface follows.
//
// Every `value` must match a `Character.ClothingStyle` choice in
// Backend/capp/models.py (the API rejects unknown values with
// `invalid_clothing_style`), and must stay within the 20-character column.
//
//   value    stored/API value
//   labelKey i18n key under `wizard.*` (en + sw in i18n/translations.js)
//   label    English fallback for views that do not use i18n
//   emoji    shown on the wizard cards and the edit-form pills
//   group    "everyday" | "traditional" (drives the wizard section headings)

export const CLOTHING_GROUP_EVERYDAY = "everyday";
export const CLOTHING_GROUP_TRADITIONAL = "traditional";

export const CLOTHING_STYLES = [
  // ---- Everyday styles ----
  {
    value: "casual",
    labelKey: "wizard.clothingCasual",
    label: "Casual",
    emoji: "👕",
    group: CLOTHING_GROUP_EVERYDAY,
  },
  {
    value: "formal",
    labelKey: "wizard.clothingFormal",
    label: "Formal",
    emoji: "👔",
    group: CLOTHING_GROUP_EVERYDAY,
  },
  {
    value: "sporty",
    labelKey: "wizard.clothingSporty",
    label: "Sporty",
    emoji: "👟",
    group: CLOTHING_GROUP_EVERYDAY,
  },
  {
    value: "elegant",
    labelKey: "wizard.clothingElegant",
    label: "Elegant",
    emoji: "👗",
    group: CLOTHING_GROUP_EVERYDAY,
  },
  {
    value: "streetwear",
    labelKey: "wizard.clothingStreetwear",
    label: "Streetwear",
    emoji: "🧢",
    group: CLOTHING_GROUP_EVERYDAY,
  },
  {
    value: "bohemian",
    labelKey: "wizard.clothingBohemian",
    label: "Bohemian",
    emoji: "🌸",
    group: CLOTHING_GROUP_EVERYDAY,
  },
  {
    value: "gothic",
    labelKey: "wizard.clothingGothic",
    label: "Gothic",
    emoji: "🦇",
    group: CLOTHING_GROUP_EVERYDAY,
  },
  {
    value: "preppy",
    labelKey: "wizard.clothingPreppy",
    label: "Preppy",
    emoji: "📚",
    group: CLOTHING_GROUP_EVERYDAY,
  },
  {
    value: "lingerie",
    labelKey: "wizard.clothingLingerie",
    label: "Lingerie / Underwear",
    emoji: "👙",
    group: CLOTHING_GROUP_EVERYDAY,
  },

  // ---- Traditional Kenyan / East African styles ----
  {
    value: "kitenge",
    labelKey: "wizard.clothingKitenge",
    label: "Kitenge",
    emoji: "🎨",
    group: CLOTHING_GROUP_TRADITIONAL,
  },
  {
    value: "kanga",
    labelKey: "wizard.clothingKanga",
    label: "Kanga",
    emoji: "🌺",
    group: CLOTHING_GROUP_TRADITIONAL,
  },
  {
    value: "swahili_coastal",
    labelKey: "wizard.clothingSwahiliCoastal",
    label: "Swahili Coastal",
    emoji: "🌴",
    group: CLOTHING_GROUP_TRADITIONAL,
  },
  {
    value: "maasai",
    labelKey: "wizard.clothingMaasai",
    label: "Maasai Shuka",
    emoji: "📿",
    group: CLOTHING_GROUP_TRADITIONAL,
  },
  {
    value: "gomesi",
    labelKey: "wizard.clothingGomesi",
    label: "Gomesi (Uganda)",
    emoji: "🎀",
    group: CLOTHING_GROUP_TRADITIONAL,
  },
  {
    value: "habesha",
    labelKey: "wizard.clothingHabesha",
    label: "Habesha Kemis",
    emoji: "🤍",
    group: CLOTHING_GROUP_TRADITIONAL,
  },
];

export const EVERYDAY_CLOTHING_STYLES = CLOTHING_STYLES.filter(
  (style) => style.group === CLOTHING_GROUP_EVERYDAY,
);

export const TRADITIONAL_CLOTHING_STYLES = CLOTHING_STYLES.filter(
  (style) => style.group === CLOTHING_GROUP_TRADITIONAL,
);

// value -> English label, for the read-only detail views that render plain
// English maps instead of going through `t()`.
export const CLOTHING_LABELS_EN = Object.fromEntries(
  CLOTHING_STYLES.map((style) => [style.value, style.label]),
);

// value -> i18n key, for the detail views that translate through `t()`.
export const CLOTHING_LABEL_KEYS = Object.fromEntries(
  CLOTHING_STYLES.map((style) => [style.value, style.labelKey]),
);
