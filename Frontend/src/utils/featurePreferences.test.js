import { describe, expect, it } from "vitest";
import { resolveFeaturePreferences } from "./featurePreferences";

const defaults = {
  reminders: true,
  deepthink: true,
  websearch: true,
  memory_editor: false,
};
const off = {
  reminders: false,
  deepthink: false,
  websearch: false,
  memory_editor: false,
};
describe("feature availability defaults", () => {
  it("enables only availability for absent preferences and partial dictionaries", () => {
    expect(resolveFeaturePreferences({})).toEqual(defaults);
    expect(resolveFeaturePreferences({ feature_preferences: {} })).toEqual(
      defaults,
    );
    expect(
      resolveFeaturePreferences({ feature_preferences: { reminders: false } }),
    ).toEqual({ ...defaults, reminders: false });
    expect(Object.keys(resolveFeaturePreferences({}))).not.toContain(
      "reasoning_effort",
    );
    expect(Object.keys(resolveFeaturePreferences({}))).not.toContain(
      "web_search_enabled",
    );
  });
  it("preserves every saved false, and memory's saved true", () => {
    expect(resolveFeaturePreferences({ feature_preferences: off })).toEqual(
      off,
    );
    expect(
      resolveFeaturePreferences({
        feature_preferences: { memory_editor: true },
      }),
    ).toEqual({ ...defaults, memory_editor: true });
  });
  it.each([null, [], "true", false, 1])(
    "fails closed for malformed dictionaries: %j",
    (feature_preferences) => {
      expect(resolveFeaturePreferences({ feature_preferences })).toEqual(off);
    },
  );
  it.each([null, undefined, "true", 1, [], {}])(
    "fails closed for explicitly invalid values: %j",
    (reminders) => {
      expect(
        resolveFeaturePreferences({ feature_preferences: { reminders } })
          .reminders,
      ).toBe(false);
    },
  );
  it("keeps guest availability off", () => {
    expect(resolveFeaturePreferences(null)).toEqual(off);
  });
});
