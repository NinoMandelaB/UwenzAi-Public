import { describe, it, expect } from "vitest";
import { translations } from "./translations";

describe("image privacy notice", () => {
  it("no longer implies only the user and admins can access images", () => {
    expect(translations.en.characters.imagePrivacyNotice).not.toMatch(
      /only you and admins/i,
    );
    expect(translations.sw.characters.imagePrivacyNotice).not.toMatch(
      /ni wewe tu na wasimamizi/i,
    );
  });

  it("discloses the providers that handle generated images", () => {
    expect(translations.en.characters.imagePrivacyNotice).toMatch(/providers/i);
    expect(translations.sw.characters.imagePrivacyNotice).toMatch(
      /watoa huduma/i,
    );
  });

  it("still tells the user the images are not public", () => {
    expect(translations.en.characters.imagePrivacyNotice).toMatch(
      /public gallery/i,
    );
    expect(translations.sw.characters.imagePrivacyNotice).toMatch(
      /nyumba ya picha ya umma/i,
    );
  });

  it("provides the legal page labels in both languages", () => {
    for (const language of ["en", "sw"]) {
      expect(translations[language].legal.back).toBeTruthy();
      expect(translations[language].legal.lastUpdated).toBeTruthy();
      expect(translations[language].legal.languageLabel).toBeTruthy();
    }
  });
});
