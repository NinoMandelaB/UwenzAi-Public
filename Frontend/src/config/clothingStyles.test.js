import { describe, expect, it } from "vitest";
import { translations } from "../i18n/translations";
import {
  CLOTHING_LABELS_EN,
  CLOTHING_LABEL_KEYS,
  CLOTHING_STYLES,
  EVERYDAY_CLOTHING_STYLES,
  TRADITIONAL_CLOTHING_STYLES,
} from "./clothingStyles";

// The Character.clothing_style column is a 20-character CharField.
const MAX_VALUE_LENGTH = 20;

function lookup(dictionary, dottedKey) {
  return dottedKey
    .split(".")
    .reduce(
      (node, part) => (node == null ? undefined : node[part]),
      dictionary,
    );
}

describe("clothing styles", () => {
  it("offers lingerie in the shared list, so the creation wizard shows it too", () => {
    expect(CLOTHING_STYLES.map((style) => style.value)).toContain("lingerie");
  });

  it("offers the traditional East African styles", () => {
    expect(TRADITIONAL_CLOTHING_STYLES.map((style) => style.value)).toEqual([
      "kitenge",
      "kanga",
      "swahili_coastal",
      "maasai",
      "gomesi",
      "habesha",
    ]);
  });

  it("keeps every value unique and within the backend column width", () => {
    const values = CLOTHING_STYLES.map((style) => style.value);
    expect(new Set(values).size).toBe(values.length);
    values.forEach((value) => {
      expect(value.length).toBeLessThanOrEqual(MAX_VALUE_LENGTH);
    });
  });

  it("gives every style an emoji and an English fallback label", () => {
    CLOTHING_STYLES.forEach((style) => {
      expect(style.emoji, style.value).toBeTruthy();
      expect(style.label, style.value).toBeTruthy();
    });
  });

  it("splits the list into the two wizard groups without losing a style", () => {
    expect(
      EVERYDAY_CLOTHING_STYLES.length + TRADITIONAL_CLOTHING_STYLES.length,
    ).toBe(CLOTHING_STYLES.length);
  });

  it("translates every label key in English and Kiswahili", () => {
    ["en", "sw"].forEach((language) => {
      CLOTHING_STYLES.forEach((style) => {
        expect(
          lookup(translations[language], style.labelKey),
          `${language}:${style.labelKey}`,
        ).toBeTruthy();
      });
      expect(
        lookup(translations[language], "wizard.clothingGroupTraditional"),
      ).toBeTruthy();
    });
  });

  it("derives the lookup maps used by the detail views from the same list", () => {
    CLOTHING_STYLES.forEach((style) => {
      expect(CLOTHING_LABELS_EN[style.value]).toBe(style.label);
      expect(CLOTHING_LABEL_KEYS[style.value]).toBe(style.labelKey);
    });
  });
});
