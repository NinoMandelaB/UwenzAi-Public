// src/utils/systemMessage.js

/**
 * Localize an ephemeral system chat message.
 *
 * The backend sends these over the WebSocket with a stable `code` plus English
 * `content`. Translating on the client keeps the copy in `translations.js`
 * rather than in Python, where the user's language is not known.
 *
 * `t()` echoes the lookup path back when a key is missing, which is what makes
 * the fallback chain work: an unknown code degrades to the server text instead
 * of rendering a raw key.
 */
export function localizeSystemMessage(t, message) {
  const code = message?.code;
  const content = message?.content || "";
  if (!code) return content;

  for (const path of [`systemMessages.${code}`, `errors.${code}`]) {
    const localized = t(path);
    if (localized !== path) return localized;
  }
  return content;
}
