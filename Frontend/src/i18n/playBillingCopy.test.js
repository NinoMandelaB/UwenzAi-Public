import { describe, it, expect } from "vitest";
import { translations } from "./translations";

const PLAY_KEYS = [
  "playBilledByGoogle",
  "playPurchaseSuccess",
  "playPurchaseAlready",
  "playPurchasePending",
  "playPurchaseCancelled",
  "playPurchaseFailed",
];

describe("Google Play purchase copy", () => {
  it("has every Play purchase message in both languages", () => {
    for (const language of ["en", "sw"]) {
      for (const key of PLAY_KEYS) {
        expect(translations[language].spark_pool[key]).toBeTruthy();
      }
    }
  });

  it("says in Kiswahili that the user is charged, not paid", () => {
    // kutozwa = to be charged; kulipwa = to be paid (to receive money).
    const sw = translations.sw.spark_pool;
    expect(sw.playBilledByGoogle).toMatch(/\butatozwa\b/i);
    expect(sw.playPurchaseFailed).toMatch(/\bumetozwa\b/i);
    expect(sw.approxConverted).toMatch(/\bunatozwa\b/i);
    for (const key of [...PLAY_KEYS, "approxConverted"]) {
      expect(sw[key]).not.toMatch(/\b(utalipwa|umelipwa|unalipwa)\b/i);
    }
  });

  it("says in Kiswahili that Google Play returns the money", () => {
    expect(translations.sw.spark_pool.playPurchaseFailed).toMatch(
      /itakurudishia pesa/i,
    );
  });
});
