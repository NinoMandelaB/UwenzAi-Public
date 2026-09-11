import { expect, it } from "vitest";
import { getAuthMessage } from "./authMessageMapper";
it.each(["en", "sw"])("explains explicit Google linking in %s", (language) => {
  const text = getAuthMessage(
    { payload: { data: "google_link_confirmation_required" } },
    "",
    language,
  );
  expect(text).toContain(language === "sw" ? "nenosiri" : "password first");
  expect(text).not.toContain("google_link_confirmation_required");
  expect(
    getAuthMessage("google_account_already_linked", "", language),
  ).toContain(
    language === "sw" ? "akaunti nyingine" : "different Google account",
  );
});
it.each(["en", "sw"])(
  "aligns password limit messages with 8–128 in %s",
  (language) => {
    expect(getAuthMessage("password_too_short", "", language)).toContain("8");
    expect(getAuthMessage("password_too_long", "", language)).toContain("128");
    expect(
      getAuthMessage("password_validation_failed", "", language),
    ).toContain(language === "sw" ? "imara" : "stronger");
  },
);
