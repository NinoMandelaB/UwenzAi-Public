import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { SEO_FILES, seoAssets } from "../../config/seoAssets";

describe("environment-specific SEO artifact preservation", () => {
  it("keeps staging blocked without emitting production metadata", () => {
    const emitFile = vi.fn();
    seoAssets("staging").generateBundle.call({ emitFile });
    expect(emitFile).not.toHaveBeenCalled();
    const robots = readFileSync("public/robots.txt", "utf8");
    expect(robots).toContain("Disallow: /");
  });
  it("emits all exact production preservation assets", () => {
    const emitFile = vi.fn();
    seoAssets("production").generateBundle.call({ emitFile });
    expect(emitFile.mock.calls.map(([asset]) => asset.fileName)).toEqual(
      SEO_FILES,
    );
    for (const [asset] of emitFile.mock.calls) {
      expect(asset.source).toBe(
        readFileSync(`seo/production/${asset.fileName}`, "utf8"),
      );
    }
    expect(emitFile.mock.calls[0][0].source).toContain("Allow: /characters");
    expect(emitFile.mock.calls[1][0].source).toContain("<urlset");
  });
  it("refuses an unknown profile rather than silently selecting an indexing policy", () => {
    expect(() => seoAssets("prodution")).toThrow("SEO_PROFILE");
  });
});
