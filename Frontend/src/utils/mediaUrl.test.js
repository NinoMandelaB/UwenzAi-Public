import { describe, expect, it, vi } from "vitest";

vi.mock("../config/constants", () => ({
  API: { BASE_URL: "https://api.example.com/" },
}));

import { resolveMediaUrl } from "./mediaUrl";

describe("resolveMediaUrl", () => {
  it("passes http(s) URLs through unchanged", () => {
    const url = "https://cdn.example.com/chat/attachments/x?sig=abc";
    expect(resolveMediaUrl(url)).toBe(url);
  });

  it("passes blob: URLs through unchanged (optimistic send previews)", () => {
    const url = "blob:https://app.example.com/7f977214-9703-4a60";
    expect(resolveMediaUrl(url)).toBe(url);
  });

  it("passes data: URLs through unchanged", () => {
    const url = "data:image/png;base64,AAAA";
    expect(resolveMediaUrl(url)).toBe(url);
  });

  it("prefixes relative API paths with the configured base", () => {
    expect(resolveMediaUrl("/api/v1/a/b/")).toBe(
      "https://api.example.com/api/v1/a/b/",
    );
    expect(resolveMediaUrl("media/x.jpg")).toBe(
      "https://api.example.com/media/x.jpg",
    );
  });

  it("returns falsy input unchanged", () => {
    expect(resolveMediaUrl(undefined)).toBeUndefined();
    expect(resolveMediaUrl("")).toBe("");
  });
});
