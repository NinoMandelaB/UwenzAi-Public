import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cwd } from "node:process";

// This is source configuration, not generated build output.
export const SEO_FILES = ["robots.txt", "sitemap.xml", "llms.txt"];

export function seoAssets(profile = "staging") {
  if (!["staging", "production"].includes(profile)) {
    throw new Error("SEO_PROFILE must be staging or production.");
  }
  let root = cwd();
  return {
    name: "environment-seo-assets",
    configResolved(config) {
      root = config.root;
    },
    generateBundle() {
      if (profile !== "production") return;
      for (const fileName of SEO_FILES) {
        // Exact production assets, not the staging public directory. Fail the
        // build if any preservation asset was omitted during a cherry-pick.
        const source = readFileSync(
          resolve(root, "seo/production", fileName),
          "utf8",
        );
        this.emitFile({ type: "asset", fileName, source });
      }
    },
  };
}
