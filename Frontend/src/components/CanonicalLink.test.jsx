import { render } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, expect, it } from "vitest";
import CanonicalLink from "./CanonicalLink";

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<CanonicalLink />} />
      </Routes>
    </MemoryRouter>,
  );
}

function canonicalHref() {
  const link = document.querySelector('link[rel="canonical"]');
  return link ? link.getAttribute("href") : null;
}

describe("CanonicalLink", () => {
  it("sets a self-referencing canonical for the current route", () => {
    renderAt("/characters");
    expect(canonicalHref()).toBe(`${window.location.origin}/characters`);
  });

  it("canonicalizes the homepage to the root URL", () => {
    renderAt("/");
    expect(canonicalHref()).toBe(`${window.location.origin}/`);
  });

  it("drops query strings from the canonical URL", () => {
    renderAt("/characters?utm_source=reddit");
    expect(canonicalHref()).toBe(`${window.location.origin}/characters`);
  });
});
