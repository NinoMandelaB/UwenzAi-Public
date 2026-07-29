// src/pages/edit/utils.js

import { INITIAL_FORM } from "../character/characterFormConstants";

export function looksLikeUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value || "",
  );
}

export function mapCharacterToForm(character) {
  if (!character) return { ...INITIAL_FORM };
  return {
    ...INITIAL_FORM,
    name: character.name || "",
    short_description: character.short_description || character.role || "",
    mood: character.mood || "",
    category: character.category || "supportive",
    intro: character.intro || "",
    personality_description: character.personality_description || "",
    prompt_template: character.prompt_template || "",
    system_instructions: character.system_instructions || "",
    greeting_message: character.greeting_message || "",
    tone_style: character.tone_style || "",
    primary_language: character.primary_language || "multi",
    avatar: null,

    gender: character.gender || "female",
    visual_style: character.visual_style || "realistic",
    country: character.country || "",
    town: character.town || "",
    age: character.age || 25,
    skin_color: character.skin_color || "medium",
    eye_color: character.eye_color || "brown",
    hair_length: character.hair_length || "medium",
    hairstyle: character.hairstyle || "medium",
    hair_color: character.hair_color || "brown",
    body_type: character.body_type || "average",
    breast_size: character.breast_size || "na",
    butt_size: character.butt_size || "na",
    chest_hair: character.chest_hair || "na",
    muscle_definition: character.muscle_definition || "na",
    beard_style: character.beard_style || "na",

    // Personality – now multi‑select (array of IDs)
    personality_ids: character.personality_ids || [],

    occupation_id: character.occupation?.id || null,
    hobby_ids: character.hobby_ids || [],
    relationship_style: character.relationship_style || "companion",
    clothing_style: character.clothing_style || "casual",

    is_active: Boolean(character.is_active),
    is_featured: Boolean(character.is_featured),
    is_public: character.is_public ?? true,
    nsfw_enabled: Boolean(character.nsfw_enabled),
    status: character.review_status || "pending",
    rejection_reason: character.rejection_reason || "",
  };
}
