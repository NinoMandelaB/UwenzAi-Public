import { describe, expect, it } from "vitest";

import { localizeSendError } from "./sendErrorMapper";

// Minimal translation function with the same {var} interpolation contract
// as LanguageContext.t: replaces {max} when provided.
const messages = {
  "errors.photo_too_large":
    "That photo is too large. Maximum size is {max} MB.",
  "errors.photo_dimensions":
    "That photo is too large. Maximum is {max} megapixels.",
};
const t = (path, vars = {}) => {
  const template = messages[path] || path;
  return Object.entries(vars).reduce(
    (text, [key, value]) => text.replace(`{${key}}`, String(value)),
    template,
  );
};

describe("localizeSendError photo limits", () => {
  it("interpolates the max MB from the backend details", () => {
    const failure = {
      code: "photo_too_large",
      status: 400,
      payload: {
        status: "fail",
        data: "photo_too_large",
        details: { max_bytes: 10485760, max_mb: 10 },
      },
    };
    expect(localizeSendError(t, failure)).toBe(
      "That photo is too large. Maximum size is 10 MB.",
    );
  });

  it("interpolates the max megapixels for dimension refusals", () => {
    const failure = {
      code: "photo_dimensions",
      status: 400,
      payload: {
        status: "fail",
        data: "photo_dimensions",
        details: { max_pixels: 25000000, max_megapixels: 25 },
      },
    };
    expect(localizeSendError(t, failure)).toBe(
      "That photo is too large. Maximum is 25 megapixels.",
    );
  });

  it("falls back to the backend default maximum when details are absent", () => {
    expect(localizeSendError(t, { code: "photo_too_large", status: 400 })).toBe(
      "That photo is too large. Maximum size is 10 MB.",
    );
  });

  it("uses the details of the 413 middleware body for a bodyless-looking failure", () => {
    const failure = {
      status: 413,
      payload: {
        status: "fail",
        data: "photo_too_large",
        details: { max_bytes: 20971520, max_mb: 20 },
      },
    };
    expect(localizeSendError(t, failure)).toBe(
      "That photo is too large. Maximum size is 20 MB.",
    );
  });
});
