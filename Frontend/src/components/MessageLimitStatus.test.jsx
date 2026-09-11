import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { LanguageProvider } from "../context/LanguageContext";
import MessageLimitStatus from "./MessageLimitStatus";

afterEach(() => {
  cleanup();
  localStorage.clear();
});
it.each(["en", "sw"])(
  "renders bilingual USER-only limits without splitting Unicode (%s)",
  (language) => {
    localStorage.setItem("eaaic-language", language);
    const tree = (value) => (
      <LanguageProvider>
        <MessageLimitStatus id="count" value={value} />
      </LanguageProvider>
    );
    const view = render(tree("\u{1F981}".repeat(800)));
    expect(screen.getByRole("status").textContent).toContain("800 / 800");
    expect(screen.queryByRole("alert")).toBe(null);
    view.rerender(tree(` ${"\u{1F981}".repeat(800)}`));
    expect(screen.getByRole("status").textContent).toContain("801 / 800");
    expect(screen.getByRole("alert").textContent).toContain(
      language === "sw" ? "majibu ya AI" : "AI replies",
    );
  },
);
