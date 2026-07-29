const CHARACTER_MESSAGE_MAP = {
  // Required fields
  name_required: "Please enter a character name.",
  short_description_required: "Please enter a short description.",
  mood_required: "Please enter a mood.",
  category_required: "Please select a category.",
  intro_required: "Please enter an intro.",
  personality_description_required:
    "Please describe the character's personality.",
  prompt_template_required: "Prompt template is required.",
  greeting_message_required: "Greeting message is required.",
  tone_style_required: "Please select a tone style.",
  avatar_required: "Please upload a character image.",

  // Length validations
  name_too_long: "Character name is too long.",
  short_description_too_long: "Short description is too long.",
  mood_too_long: "Mood is too long.",
  intro_too_long: "Intro is too long.",
  tone_style_too_long: "Tone style is too long.",

  // Avatar validation
  avatar_invalid_type: "Only JPG, PNG, or WebP images are allowed.",
  avatar_file_too_large: "Image must be 5MB or smaller.",
  avatar_invalid_image: "The uploaded file is not a valid image.",
  avatar_too_small: "Image must be at least 400×400 pixels.",

  // Choice validations
  invalid_tone_style: "Selected tone style is invalid.",
  invalid_primary_language: "Selected language is invalid.",
  invalid_category: "Selected category is invalid.",

  // Status / admin
  invalid_status: "Invalid character status.",
  only_approved_characters_can_be_activated:
    "Only approved characters can be activated.",

  // Permissions
  permission_denied: "You do not have permission to perform this action.",

  // General
  character_required: "Character is required.",
  character_id_required: "Character ID is required.",
  slug_required: "Character slug is required.",
  character_not_found: "Character not found.",
  authentication_required: "Please log in to continue.",

  // Uniqueness
  character_name_already_exists: "A character with this name already exists.",

  // Success messages
  character_created_successfully: "Character created successfully.",
  character_updated_successfully: "Character updated successfully.",
  character_deleted_successfully: "Character deleted successfully.",
  character_status_updated: "Character status updated.",
  character_status_unchanged: "No changes were made.",
  character_featured_status_updated: "Featured status updated.",
  character_featured_status_unchanged: "No changes were made.",
  character_nsfw_status_updated: "NSFW setting updated.",
  character_nsfw_status_unchanged: "No changes were made.",
  character_approved_successfully: "Character approved successfully.",
  character_rejected_successfully: "Character rejected successfully.",

  // Billing
  insufficient_sparks: "You don't have enough sparks for this.",
  billing_error:
    "We could not process the charge for this. No sparks were taken — please try again.",
  previous_attempt_failed:
    "The last attempt failed and your sparks were returned. Please try again.",
  invalid_idempotency_key:
    "Something went wrong sending that request. Please refresh the page and try again.",

  // System
  integrity_error: "We could not complete your request due to a data conflict.",
  server_error: "Something went wrong. Please try again.",

  // ============================================================
  // WIZARD FIELDS - LOCATION
  // ============================================================
  country_too_long: "Country name is too long.",
  town_too_long: "Town name is too long.",

  // ============================================================
  // WIZARD FIELDS - APPEARANCE
  // ============================================================
  invalid_visual_style: "Please select a valid visual style.",
  age_out_of_range: "Age must be between 18 and 100.",
  age_must_be_integer: "Age must be a valid number.",
  invalid_skin_color: "Please select a valid skin tone.",
  invalid_hairstyle: "Please select a valid hairstyle.",
  invalid_hair_color: "Please select a valid hair color.",
  invalid_body_type: "Please select a valid body type.",

  // ============================================================
  // WIZARD FIELDS - GENDER SPECIFIC
  // ============================================================
  invalid_breast_size: "Invalid breast size selection.",
  invalid_butt_size: "Invalid butt size selection.",
  invalid_chest_hair: "Invalid chest hair selection.",
  invalid_muscle_definition: "Invalid muscle definition selection.",
  invalid_beard_style: "Invalid beard style selection.",
  male_breast_size_must_be_na: "Male characters cannot have a breast size.",
  male_butt_size_must_be_na: "Male characters cannot have a butt size.",
  female_chest_hair_must_be_na: "Female characters cannot have chest hair.",
  female_muscle_definition_must_be_na:
    "Female characters cannot have muscle definition.",
  female_beard_style_must_be_na: "Female characters cannot have a beard style.",

  // ============================================================
  // WIZARD FIELDS - PERSONALITY & LIFESTYLE
  // ============================================================
  invalid_personality: "Please select a valid personality.",
  invalid_relationship_style: "Please select a valid relationship style.",
  invalid_clothing_style: "Please select a valid clothing style.",
  invalid_avatar_source: "Invalid avatar source.",

  // ============================================================
  // OCCUPATION SERVICE
  // ============================================================
  occupation_already_exists: "An occupation with this name already exists.",
  occupation_created_successfully: "Occupation created successfully.",

  // ============================================================
  // HOBBY SERVICE
  // ============================================================
  hobby_already_exists: "A hobby with this name already exists.",
  hobby_created_successfully: "Hobby created successfully.",

  // ============================================================
  // GALLERY SERVICE
  // ============================================================
  gallery_image_not_found: "Gallery image not found.",
  gallery_image_uploaded_successfully: "Image uploaded successfully.",
  gallery_image_deleted_successfully: "Image deleted successfully.",
  caption_too_long: "Caption is too long (maximum 200 characters).",
  image_required: "Please select an image to upload.",
  image_invalid_type: "Only JPG, PNG, or WebP images are allowed.",
  image_file_too_large: "Image must be 5MB or smaller.",
  image_invalid_image: "The uploaded file is not a valid image.",
  image_too_small: "Image must be at least 400×400 pixels.",
};

export function getCharacterMessage(
  errorOrCode,
  fallback = "Something went wrong.",
) {
  if (!errorOrCode) return fallback;

  if (typeof errorOrCode === "string") {
    return (
      CHARACTER_MESSAGE_MAP[errorOrCode] ||
      prettifyCode(errorOrCode) ||
      fallback
    );
  }

  const directPayload =
    errorOrCode?.response?.data?.data ??
    errorOrCode?.response?.data?.message ??
    errorOrCode?.data?.data ??
    errorOrCode?.data?.message ??
    errorOrCode?.message;

  if (typeof directPayload === "string") {
    return (
      CHARACTER_MESSAGE_MAP[directPayload] ||
      prettifyCode(directPayload) ||
      fallback
    );
  }

  if (typeof directPayload === "object" && directPayload) {
    if (typeof directPayload.code === "string") {
      return (
        CHARACTER_MESSAGE_MAP[directPayload.code] ||
        prettifyCode(directPayload.code) ||
        fallback
      );
    }
  }

  return fallback;
}

export function getCharacterTitle(errorOrCode, fallback = "Request failed") {
  if (!errorOrCode) return fallback;

  const code =
    typeof errorOrCode === "string"
      ? errorOrCode
      : errorOrCode?.response?.data?.data ||
        errorOrCode?.response?.data?.message ||
        errorOrCode?.data?.data ||
        errorOrCode?.data?.message;

  if (typeof code !== "string") return fallback;

  if (code === "permission_denied") return "Access denied";
  if (code === "authentication_required") return "Authentication required";
  if (code === "character_not_found") return "Not found";
  if (code === "character_name_already_exists") return "Duplicate name";

  if (code === "avatar_invalid_type" || code === "avatar_invalid_image") {
    return "Invalid image";
  }

  if (code === "avatar_file_too_large") return "Image too large";
  if (code === "avatar_too_small") return "Image too small";

  if (
    code === "name_required" ||
    code === "short_description_required" ||
    code === "mood_required" ||
    code === "category_required" ||
    code === "intro_required" ||
    code === "personality_description_required" ||
    code === "prompt_template_required" ||
    code === "greeting_message_required" ||
    code === "tone_style_required" ||
    code === "avatar_required"
  ) {
    return "Missing required fields";
  }

  if (
    code === "name_too_long" ||
    code === "short_description_too_long" ||
    code === "mood_too_long" ||
    code === "intro_too_long" ||
    code === "tone_style_too_long" ||
    code === "country_too_long" ||
    code === "town_too_long" ||
    code === "caption_too_long"
  ) {
    return "Invalid field length";
  }

  if (
    code === "invalid_tone_style" ||
    code === "invalid_primary_language" ||
    code === "invalid_category" ||
    code === "invalid_status" ||
    code === "invalid_visual_style" ||
    code === "invalid_skin_color" ||
    code === "invalid_hairstyle" ||
    code === "invalid_hair_color" ||
    code === "invalid_body_type" ||
    code === "invalid_breast_size" ||
    code === "invalid_butt_size" ||
    code === "invalid_chest_hair" ||
    code === "invalid_muscle_definition" ||
    code === "invalid_beard_style" ||
    code === "invalid_personality" ||
    code === "invalid_relationship_style" ||
    code === "invalid_clothing_style" ||
    code === "invalid_avatar_source"
  ) {
    return "Invalid selection";
  }

  if (code === "age_out_of_range" || code === "age_must_be_integer") {
    return "Invalid age";
  }

  if (
    code === "male_breast_size_must_be_na" ||
    code === "male_butt_size_must_be_na" ||
    code === "female_chest_hair_must_be_na" ||
    code === "female_muscle_definition_must_be_na" ||
    code === "female_beard_style_must_be_na"
  ) {
    return "Invalid attribute for gender";
  }

  if (code === "occupation_already_exists" || code === "hobby_already_exists") {
    return "Duplicate entry";
  }

  if (
    code === "image_required" ||
    code === "image_invalid_type" ||
    code === "image_file_too_large" ||
    code === "image_invalid_image" ||
    code === "image_too_small"
  ) {
    return "Invalid image";
  }

  if (code === "gallery_image_not_found") return "Not found";

  if (
    code === "character_created_successfully" ||
    code === "character_updated_successfully" ||
    code === "character_deleted_successfully" ||
    code === "character_approved_successfully" ||
    code === "character_rejected_successfully" ||
    code === "occupation_created_successfully" ||
    code === "hobby_created_successfully" ||
    code === "gallery_image_uploaded_successfully" ||
    code === "gallery_image_deleted_successfully"
  ) {
    return "Success";
  }

  if (
    code === "character_status_updated" ||
    code === "character_featured_status_updated" ||
    code === "character_nsfw_status_updated"
  ) {
    return "Status Updated";
  }

  return fallback;
}

function prettifyCode(value) {
  if (typeof value !== "string") return "";
  if (!value.includes("_")) return value;
  const formatted = value.replace(/_/g, " ").trim();
  return formatted.charAt(0).toUpperCase() + formatted.slice(1) + ".";
}
