export const LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "sw", label: "Swahili" },
  { value: "sh", label: "Sheng" },
  { value: "multi", label: "Multilingual" },
];

export const TONE_OPTIONS = [
  { value: "warm", label: "Warm & Friendly" },
  { value: "playful", label: "Playful & Fun" },
  { value: "bold", label: "Bold & Confident" },
  { value: "calm", label: "Calm & Thoughtful" },
  { value: "romantic", label: "Romantic & Flirty" },
  { value: "professional", label: "Professional & Direct" },
];

export const CATEGORY_OPTIONS = [
  { value: "supportive", label: "Supportive" },
  { value: "fun", label: "Fun" },
  { value: "romantic", label: "Romantic" },
];

export const INITIAL_FORM = {
  name: "",
  short_description: "",
  mood: "",
  category: "supportive",
  intro: "",
  personality_description: "",
  prompt_template: "",
  system_instructions: "",
  greeting_message: "",
  tone_style: "",
  primary_language: "multi",
  avatar: null,
};

export const ACCEPTED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"];

export const MAX_AVATAR_FILE_SIZE_BYTES = 5 * 1024 * 1024;
export const MAX_AVATAR_FILE_SIZE_MB = 5;

export const MIN_AVATAR_WIDTH = 400;
export const MIN_AVATAR_HEIGHT = 400;
