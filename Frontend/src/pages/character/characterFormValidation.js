import {
  ACCEPTED_IMAGE_TYPES,
  MAX_AVATAR_FILE_SIZE_BYTES,
  MAX_AVATAR_FILE_SIZE_MB,
  MIN_AVATAR_WIDTH,
  MIN_AVATAR_HEIGHT,
  TONE_OPTIONS,
  LANGUAGE_OPTIONS,
  CATEGORY_OPTIONS,
} from "./characterFormConstants";

const VALID_TONE_VALUES = new Set(TONE_OPTIONS.map((option) => option.value));
const VALID_LANGUAGE_VALUES = new Set(
  LANGUAGE_OPTIONS.map((option) => option.value),
);
const VALID_CATEGORY_VALUES = new Set(
  CATEGORY_OPTIONS.map((option) => option.value),
);

export function validateCharacterForm(form, options = {}) {
  const { requireAvatar = true } = options;
  const errors = {};

  const name = form.name?.trim() || "";
  const shortDescription = form.short_description?.trim() || "";
  const mood = form.mood?.trim() || "";
  const intro = form.intro?.trim() || "";
  const personalityDescription = form.personality_description?.trim() || "";
  const promptTemplate = form.prompt_template?.trim() || "";
  const systemInstructions = form.system_instructions?.trim() || "";
  const greetingMessage = form.greeting_message?.trim() || "";
  const toneStyle = form.tone_style?.trim() || "";
  const primaryLanguage = form.primary_language?.trim() || "";
  const category = form.category?.trim() || "";

  if (!name) {
    errors.name = "Character name is required.";
  } else if (name.length > 150) {
    errors.name = "Character name must be 150 characters or fewer.";
  }

  if (!shortDescription) {
    errors.short_description = "Short description is required.";
  } else if (shortDescription.length > 255) {
    errors.short_description =
      "Short description must be 255 characters or fewer.";
  }

  if (!mood) {
    errors.mood = "Mood is required.";
  } else if (mood.length > 80) {
    errors.mood = "Mood must be 80 characters or fewer.";
  }

  if (!category) {
    errors.category = "Category is required.";
  } else if (!VALID_CATEGORY_VALUES.has(category)) {
    errors.category = "Please choose a valid category.";
  }

  if (!intro) {
    errors.intro = "Intro is required.";
  } else if (intro.length > 255) {
    errors.intro = "Intro must be 255 characters or fewer.";
  }

  if (!personalityDescription) {
    errors.personality_description = "Personality description is required.";
  }

  if (!greetingMessage) {
    errors.greeting_message = "Greeting message is required.";
  }

  if (!promptTemplate) {
    errors.prompt_template = "Prompt template is required.";
  }

  if (systemInstructions.length > 0 && systemInstructions.length > 10000) {
    errors.system_instructions =
      "System instructions must be 10000 characters or fewer.";
  }

  if (!toneStyle) {
    errors.tone_style = "Tone style is required.";
  } else if (toneStyle.length > 100) {
    errors.tone_style = "Tone style must be 100 characters or fewer.";
  } else if (!VALID_TONE_VALUES.has(toneStyle)) {
    errors.tone_style = "Please choose a valid tone style.";
  }

  if (!primaryLanguage) {
    errors.primary_language = "Primary language is required.";
  } else if (!VALID_LANGUAGE_VALUES.has(primaryLanguage)) {
    errors.primary_language = "Please choose a valid primary language.";
  }

  if (requireAvatar && !form.avatar) {
    errors.avatar = "Character image is required.";
  }

  return errors;
}

export function validateAvatarFile(file) {
  if (!file) {
    return "Character image is required.";
  }

  if (!ACCEPTED_IMAGE_TYPES.includes(file.type)) {
    return "Only JPG, PNG, or WEBP images are allowed.";
  }

  if (file.size > MAX_AVATAR_FILE_SIZE_BYTES) {
    return `Image must be ${MAX_AVATAR_FILE_SIZE_MB}MB or smaller.`;
  }

  return "";
}

export async function validateAvatarDimensions(file) {
  if (!file) return "";

  let objectUrl = "";

  try {
    objectUrl = URL.createObjectURL(file);

    const dimensions = await readImageDimensions(objectUrl);

    if (
      dimensions.width < MIN_AVATAR_WIDTH ||
      dimensions.height < MIN_AVATAR_HEIGHT
    ) {
      return `Image must be at least ${MIN_AVATAR_WIDTH}x${MIN_AVATAR_HEIGHT}px.`;
    }

    return "";
  } catch {
    return "Invalid image file.";
  } finally {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
    }
  }
}

function readImageDimensions(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();

    image.onload = () => {
      resolve({
        width: image.naturalWidth,
        height: image.naturalHeight,
      });
    };

    image.onerror = () => {
      reject(new Error("Failed to load image."));
    };

    image.src = src;
  });
}
