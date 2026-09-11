// Match Python len(): Unicode code points, not UTF-16 code units. This is a
// USER-input policy only; never truncate generated replies or stored history.
export const USER_MESSAGE_LIMIT = 800;
export const SUMMARY_EDIT_LIMIT = 12000;
export const characterCount = (text) =>
  typeof text === "string" ? [...text].length : 0;
export const validUserMessage = (text) =>
  typeof text === "string" &&
  Boolean(text.trim()) &&
  characterCount(text) <= USER_MESSAGE_LIMIT;
