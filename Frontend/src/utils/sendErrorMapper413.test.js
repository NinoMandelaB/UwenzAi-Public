import { describe, expect, it } from "vitest";

import { localizeSendError } from "./sendErrorMapper";

const t = (path) =>
  path === "errors.photo_too_large"
    ? "Picha ni kubwa mno"
    : path === "errors.photo_invalid_image"
      ? "Picha si sahihi"
      : path;

describe("localizeSendError status 413", () => {
  it("maps a bodyless 413 to the localized photo_too_large message", () => {
    expect(localizeSendError(t, { status: 413 })).toBe("Picha ni kubwa mno");
  });

  it("prefers an explicit, translated error code over the status fallback", () => {
    expect(
      localizeSendError(t, { status: 413, code: "photo_invalid_image" }),
    ).toBe("Picha si sahihi");
  });
});
