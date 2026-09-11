// Availability only. These defaults never enable search or deep reasoning
// inside a conversation, and never write preferences on the user's behalf.
const DEFAULTS = Object.freeze({
  reminders: true,
  deepthink: true,
  websearch: true,
  memory_editor: false,
});

export function resolveFeaturePreferences(user) {
  const stored = user?.feature_preferences;
  const malformed =
    stored !== undefined &&
    (stored === null || typeof stored !== "object" || Array.isArray(stored));
  return Object.fromEntries(
    Object.entries(DEFAULTS).map(([key, fallback]) => [
      key,
      !user || malformed
        ? false
        : Object.prototype.hasOwnProperty.call(stored || {}, key)
          ? stored[key] === true
          : fallback,
    ]),
  );
}
