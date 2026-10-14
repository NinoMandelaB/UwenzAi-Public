import { API } from "../config/constants";
import { apiFetch } from "./apiClient";
import { idempotencyHeaders } from "../utils/idempotency";

function createApiError({
  message = "Request failed.",
  status = null,
  payload = null,
  code = null,
}) {
  const error = new Error(message);
  error.status = status;
  error.payload = payload;
  error.code = code;
  return error;
}

async function parseJsonSafely(response) {
  const contentType = response.headers.get("content-type") || "";

  if (!contentType.includes("application/json")) {
    return null;
  }

  try {
    return await response.json();
  } catch {
    return null;
  }
}

function extractErrorMessage(payload) {
  if (!payload) {
    return { message: "Request failed.", code: null };
  }

  if (typeof payload?.data === "string") {
    return { message: payload.data, code: payload.data };
  }

  if (typeof payload?.data?.code === "string") {
    return {
      message: payload.data.code,
      code: payload.data.code,
    };
  }

  if (Array.isArray(payload?.data?.errors) && payload.data.errors.length > 0) {
    return {
      message: payload.data.errors.join(" "),
      code: payload?.data?.code || null,
    };
  }

  if (typeof payload?.message === "string") {
    return { message: payload.message, code: payload.message };
  }

  if (typeof payload?.detail === "string") {
    return { message: payload.detail, code: payload.detail };
  }

  return { message: "Request failed.", code: null };
}

async function handleResponse(response) {
  const data = await parseJsonSafely(response);

  if (!response.ok || data?.status !== "success") {
    const extracted = extractErrorMessage(data);

    throw createApiError({
      message: extracted.message,
      code: extracted.code,
      status: response.status,
      payload: data,
    });
  }

  return data;
}

function appendIfDefined(formData, key, value) {
  if (value === undefined || value === null || value === "") {
    return;
  }

  formData.append(key, value);
}

function buildCharacterFormData(payload = {}, options = {}) {
  const { includeAvatar = true } = options;
  const formData = new FormData();

  // Core fields (original)
  appendIfDefined(formData, "name", payload.name);
  appendIfDefined(
    formData,
    "short_description",
    payload.short_description ?? payload.role,
  );
  appendIfDefined(formData, "is_public", payload.is_public);
  appendIfDefined(formData, "mood", payload.mood);
  appendIfDefined(formData, "category", payload.category);
  appendIfDefined(formData, "intro", payload.intro);
  appendIfDefined(
    formData,
    "personality_description",
    payload.personality_description,
  );
  appendIfDefined(formData, "prompt_template", payload.prompt_template);
  appendIfDefined(formData, "system_instructions", payload.system_instructions);
  appendIfDefined(formData, "greeting_message", payload.greeting_message);
  appendIfDefined(formData, "tone_style", payload.tone_style);
  appendIfDefined(formData, "primary_language", payload.primary_language);
  appendIfDefined(formData, "is_active", payload.is_active);
  appendIfDefined(formData, "is_featured", payload.is_featured);
  appendIfDefined(formData, "nsfw_enabled", payload.nsfw_enabled);
  appendIfDefined(formData, "status", payload.status);
  appendIfDefined(formData, "rejection_reason", payload.rejection_reason);

  // ---------- WIZARD FIELDS (NEW) ----------
  appendIfDefined(formData, "gender", payload.gender);
  appendIfDefined(formData, "visual_style", payload.visual_style);
  appendIfDefined(formData, "is_public", payload.is_public);
  appendIfDefined(formData, "country", payload.country);
  appendIfDefined(formData, "town", payload.town);
  appendIfDefined(formData, "age", payload.age);
  appendIfDefined(formData, "skin_color", payload.skin_color);
  appendIfDefined(formData, "eye_color", payload.eye_color);
  appendIfDefined(formData, "hair_length", payload.hair_length);
  appendIfDefined(formData, "hairstyle", payload.hairstyle);
  appendIfDefined(formData, "hair_color", payload.hair_color);
  appendIfDefined(formData, "body_type", payload.body_type);
  appendIfDefined(formData, "breast_size", payload.breast_size);
  appendIfDefined(formData, "butt_size", payload.butt_size);
  appendIfDefined(formData, "chest_hair", payload.chest_hair);
  appendIfDefined(formData, "muscle_definition", payload.muscle_definition);
  appendIfDefined(formData, "beard_style", payload.beard_style);
  // REMOVED: appendIfDefined(formData, "personality", payload.personality);
  appendIfDefined(formData, "occupation_id", payload.occupation_id);
  appendIfDefined(formData, "relationship_style", payload.relationship_style);
  appendIfDefined(formData, "clothing_style", payload.clothing_style);
  appendIfDefined(formData, "avatar_source", payload.avatar_source);

  // Hobbies (array -> multiple entries)
  if (payload.hobby_ids && Array.isArray(payload.hobby_ids)) {
    payload.hobby_ids.forEach((id) => {
      formData.append("hobby_ids", String(id));
    });
  }

  // Personalities (array -> multiple entries)
  if (payload.personality_ids && Array.isArray(payload.personality_ids)) {
    payload.personality_ids.forEach((id) => {
      formData.append("personality_ids", String(id));
    });
  }
  // ----------------------------------------

  if (includeAvatar && payload.avatar) {
    formData.append("avatar", payload.avatar);
  }

  return formData;
}

function buildQueryString(params = {}) {
  const searchParams = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }

    searchParams.append(key, String(value));
  });

  const query = searchParams.toString();
  return query ? `?${query}` : "";
}

export async function listFlaggedCharacters() {
  const response = await apiFetch(API.CHARACTERS.FLAGGED, {
    method: "GET",
  });
  return response.json();
}

export async function flagCharacter(characterId, reason) {
  const response = await apiFetch(API.CHARACTERS.FLAG(characterId), {
    method: "POST",
    body: { reason },
  });
  return response.json();
}

export const listMyCharacters = async () => {
  const response = await apiFetch(API.CHARACTERS.MY_CHARACTERS, {
    method: "GET",
    skipCsrf: true,
  });
  return response.json();
};

export async function listCharacters(params = {}) {
  const query = buildQueryString({
    include_inactive: params.includeInactive,
    featured_only: params.featuredOnly,
    popular: params.popular,
    category: params.category,
    language: params.language,
    status: params.status,
    search: params.search,
    mine: params.mine,
  });

  const response = await apiFetch(`${API.CHARACTERS.BASE}${query}`, {
    method: "GET",
  });

  return handleResponse(response);
}

/**
 * Fetches characters with 'pending' status.
 * Uses the internal apiFetch and handleResponse for consistent error handling.
 */
export async function listPendingCharacters() {
  const response = await apiFetch(API.CHARACTERS.PENDING, {
    method: "GET",
  });

  return handleResponse(response);
}

export async function getCharacterById(characterId) {
  const response = await apiFetch(API.CHARACTERS.GET_BY_ID(characterId), {
    method: "GET",
  });

  return handleResponse(response);
}

export async function getCharacterBySlug(slug) {
  const response = await apiFetch(API.CHARACTERS.GET_BY_SLUG(slug), {
    method: "GET",
  });

  return handleResponse(response);
}

// createCharacter, updateCharacter, generateAvatar and generateGalleryImage
// are paid. Each takes an operationId that the caller keeps for the lifetime
// of one user action and reuses on retry; see src/utils/idempotency.js.

export async function createCharacter(payload, operationId) {
  const formData = buildCharacterFormData(payload, { includeAvatar: true });

  const response = await apiFetch(API.CHARACTERS.CREATE, {
    method: "POST",
    body: formData,
    headers: idempotencyHeaders(operationId),
  });

  return handleResponse(response);
}

export async function updateCharacter(characterId, payload, operationId) {
  const formData = buildCharacterFormData(payload, { includeAvatar: true });

  const response = await apiFetch(API.CHARACTERS.UPDATE(characterId), {
    method: "PATCH",
    body: formData,
    headers: idempotencyHeaders(operationId),
  });

  return handleResponse(response);
}

export async function setCharacterActiveStatus(characterId, isActive) {
  const response = await apiFetch(API.CHARACTERS.SET_ACTIVE(characterId), {
    method: "POST",
    body: {
      is_active: isActive,
    },
  });

  return handleResponse(response);
}

export async function setCharacterFeaturedStatus(characterId, isFeatured) {
  const response = await apiFetch(API.CHARACTERS.SET_FEATURED(characterId), {
    method: "POST",
    body: {
      is_featured: isFeatured,
    },
  });

  return handleResponse(response);
}

export async function setCharacterNsfwStatus(characterId, nsfwEnabled) {
  const response = await apiFetch(API.CHARACTERS.SET_NSFW(characterId), {
    method: "POST",
    body: {
      nsfw_enabled: nsfwEnabled,
    },
  });

  return handleResponse(response);
}

export async function approveCharacter(characterId, isActive = true) {
  const response = await apiFetch(API.CHARACTERS.APPROVE(characterId), {
    method: "POST",
    body: {
      is_active: isActive,
    },
  });

  return handleResponse(response);
}

export async function rejectCharacter(characterId, rejectionReason = "") {
  const response = await apiFetch(API.CHARACTERS.REJECT(characterId), {
    method: "POST",
    body: {
      rejection_reason: rejectionReason,
    },
  });

  return handleResponse(response);
}

export async function deleteCharacter(characterId) {
  const response = await apiFetch(API.CHARACTERS.DELETE(characterId), {
    method: "POST",
  });

  return handleResponse(response);
}

// ============================================================
// WIZARD SUPPORT: OCCUPATIONS
// ============================================================

/**
 * Fetch all available occupations
 */
export async function listOccupations() {
  const response = await apiFetch(API.OCCUPATIONS.LIST, {
    method: "GET",
  });

  return handleResponse(response);
}

/**
 * Create a new occupation (admin only)
 * @param {Object} payload - { name, description }
 */
export async function createOccupation(payload) {
  const response = await apiFetch(API.OCCUPATIONS.CREATE, {
    method: "POST",
    body: {
      name: payload.name,
      description: payload.description,
    },
  });

  return handleResponse(response);
}

// ============================================================
// WIZARD SUPPORT: HOBBIES
// ============================================================

/**
 * Fetch all available hobbies
 */
export async function listHobbies() {
  const response = await apiFetch(API.HOBBIES.LIST, {
    method: "GET",
  });

  return handleResponse(response);
}

/**
 * Create a new hobby (admin only)
 * @param {Object} payload - { name, description }
 */
export async function createHobby(payload) {
  const response = await apiFetch(API.HOBBIES.CREATE, {
    method: "POST",
    body: {
      name: payload.name,
      description: payload.description,
    },
  });

  return handleResponse(response);
}

// ============================================================
// GALLERY IMAGES
// ============================================================

/**
 * Fetch all gallery images for a character
 * @param {string} characterId - UUID of the character
 */
export async function listGalleryImages(characterId) {
  const response = await apiFetch(API.CHARACTERS.GALLERY.LIST(characterId), {
    method: "GET",
  });

  return handleResponse(response);
}

/**
 * Upload a gallery image for a character (admin only)
 * @param {string} characterId - UUID of the character
 * @param {File} imageFile - Image file to upload
 * @param {Object} options - { caption, order }
 */
export async function uploadGalleryImage(characterId, imageFile, options = {}) {
  const { caption = "", order = 0 } = options;
  const formData = new FormData();

  formData.append("image", imageFile);

  if (caption) {
    formData.append("caption", caption);
  }

  if (order !== undefined && order !== null) {
    formData.append("order", String(order));
  }

  const response = await apiFetch(API.CHARACTERS.GALLERY.UPLOAD(characterId), {
    method: "POST",
    body: formData,
  });

  return handleResponse(response);
}

/**
 * Delete a gallery image (admin only)
 * @param {string} imageId - UUID of the gallery image
 */
export async function deleteGalleryImage(imageId) {
  const response = await apiFetch(API.CHARACTERS.GALLERY.DELETE(imageId), {
    method: "DELETE",
  });

  return handleResponse(response);
}

// ============================================================
// AI IMAGE GENERATION
// ============================================================

/**
 * Generate an AI avatar for a character
 * @param {string} characterId - UUID of the character
 * @param {Object} options - { prompt, negative_prompt, aspect_ratio, style_preset, seed, extra_prompt }
 */
export async function generateAvatar(characterId, options = {}) {
  const {
    prompt = "portrait, high quality, detailed",
    negative_prompt = "",
    aspect_ratio = "1:1",
    style_preset = "photographic",
    seed = 0,
    extra_prompt = "", // <-- new
  } = options;

  const body = {
    prompt,
    negative_prompt,
    aspect_ratio,
    style_preset,
    seed,
    extra_prompt, // always included, even if empty
  };

  const response = await apiFetch(API.CHARACTERS.GENERATE_AVATAR(characterId), {
    method: "POST",
    body,
    headers: idempotencyHeaders(options.operationId),
  });

  return handleResponse(response);
}

/**
 * Generate an AI gallery image for a character
 * @param {string} characterId - UUID of the character
 * @param {Object} options - { prompt, caption, order, negative_prompt, aspect_ratio, style_preset, seed, extra_prompt, publish_on_approval }
 *
 * `publish_on_approval` is set only by the character setup and edit pages:
 * the picture is then part of the character's own gallery and becomes visible
 * to everyone once an admin approves the character. Pictures generated on the
 * detail page or in chat leave it out and stay private to their creator.
 */
export async function generateGalleryImage(characterId, options = {}) {
  const {
    prompt = "portrait, high quality, detailed",
    caption = "",
    order = null,
    negative_prompt = "",
    aspect_ratio = "2:3",
    style_preset = "photographic",
    seed = 0,
    extra_prompt = "", // <-- new
    publish_on_approval = false,
  } = options;

  const body = {
    prompt,
    negative_prompt,
    aspect_ratio,
    style_preset,
    seed,
    extra_prompt, // always included, even if empty
  };

  if (publish_on_approval) {
    body.publish_on_approval = true;
  }
  if (caption) {
    body.caption = caption;
  }
  if (order !== null && order !== undefined) {
    body.order = order;
  }

  const response = await apiFetch(
    API.CHARACTERS.GALLERY.GENERATE(characterId),
    {
      method: "POST",
      body,
      headers: idempotencyHeaders(options.operationId),
    },
  );

  return handleResponse(response);
}

// ============================================================
// CHARACTER SETUP COMPLETION
// ============================================================

/**
 * Complete character setup and transition from DRAFT to PENDING/APPROVED
 * @param {string} characterId - UUID of the character
 * @param {Object} options - { avatar_source } (optional, defaults to 'ai_generated')
 */
export async function completeCharacterSetup(characterId, options = {}) {
  const { avatar_source = "ai_generated" } = options;

  const response = await apiFetch(API.CHARACTERS.COMPLETE_SETUP(characterId), {
    method: "POST",
    body: {
      avatar_source,
    },
  });

  return handleResponse(response);
}

// ============================================================
// WIZARD HELPER: Build full wizard payload for creation
// ============================================================

/**
 * Build a complete character creation payload from wizard state
 * This function is pure – it returns an object ready for createCharacter()
 *
 * @param {Object} wizardState - The accumulated wizard selections
 * @returns {Object} - Payload compatible with createCharacter (as JSON)
 */
export function buildWizardPayload(wizardState) {
  const {
    // Step 1
    name,
    gender,
    visual_style,
    is_public,
    // Location
    country,
    town,
    // Step 2
    age,
    skin_color,
    eye_color,
    // Step 3
    hair_length,
    hairstyle,
    hair_color,
    // Step 4
    body_type,
    breast_size,
    butt_size,
    chest_hair,
    muscle_definition,
    beard_style,
    // Step 5
    personality,
    // Step 6
    occupation_id,
    hobby_ids,
    // Step 7
    relationship_style,
    clothing_style,
    // Common required fields (minimal defaults)
    short_description = "A custom AI companion.",
    mood = "Friendly",
    category = "supportive",
    intro = "Hello! I'm excited to meet you.",
    personality_description = "A unique personality created just for you.",
    prompt_template = "You are {{char_name}}, a {{gender}} AI companion. Be engaging and supportive.",
    greeting_message = "Hi there!",
    tone_style = "warm",
    primary_language = "en",
  } = wizardState;

  return {
    name,
    gender,
    visual_style,
    is_public,
    country,
    town,
    age,
    skin_color,
    eye_color,
    hair_length,
    hairstyle,
    hair_color,
    body_type,
    breast_size,
    butt_size,
    chest_hair,
    muscle_definition,
    beard_style,
    personality,
    occupation_id,
    hobby_ids: hobby_ids || [],
    relationship_style,
    clothing_style,
    short_description,
    mood,
    category,
    intro,
    personality_description,
    prompt_template,
    greeting_message,
    tone_style,
    primary_language,
    avatar_source: wizardState.avatar_source || "ai_generated",
  };
}

export async function clearCharacterFlags(characterId) {
  const response = await apiFetch(API.CHARACTERS.CLEAR_FLAGS(characterId), {
    method: "DELETE",
  });
  return handleResponse(response);
}

/**
 * Create a character from wizard state.
 * This handles converting the wizard state to the expected multipart form.
 *
 * @param {Object} wizardState - The accumulated wizard selections
 * @returns {Promise} - Result from createCharacter
 */
export async function createCharacterFromWizard(wizardState, operationId) {
  const payload = buildWizardPayload(wizardState);

  const formData = buildCharacterFormData(
    {
      ...payload,
      avatar: wizardState.avatarFile || null,
    },
    { includeAvatar: true },
  );

  const response = await apiFetch(API.CHARACTERS.CREATE, {
    method: "POST",
    body: formData,
    headers: idempotencyHeaders(operationId),
  });

  return handleResponse(response);
}
