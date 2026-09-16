// src/pages/NotFound.test.jsx
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NotFound from "./NotFound";

const t = (key) => key;
vi.mock("../context/LanguageContext", () => ({ useLanguage: () => ({ t }) }));

beforeEach(() => {
  document.title = "UwenzAi";
});

afterEach(cleanup);

describe("NotFound", () => {
  it("renders the 404 page with a way back to the app", () => {
    render(
      <MemoryRouter>
        <NotFound />
      </MemoryRouter>,
    );
    expect(screen.getByText("404")).toBeTruthy();
    expect(screen.getByText("notFound.title")).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "notFound.backHome" })
        .getAttribute("href"),
    ).toBe("/characters");
  });

  it("sets a robots noindex meta while mounted and removes it on unmount", () => {
    expect(document.querySelector('meta[name="robots"]')).toBeNull();

    const { unmount } = render(
      <MemoryRouter>
        <NotFound />
      </MemoryRouter>,
    );
    const meta = document.querySelector('meta[name="robots"]');
    expect(meta).toBeTruthy();
    expect(meta.getAttribute("content")).toBe("noindex");

    unmount();
    expect(document.querySelector('meta[name="robots"]')).toBeNull();
  });

  it("restores a pre-existing robots meta on unmount", () => {
    const existing = document.createElement("meta");
    existing.setAttribute("name", "robots");
    existing.setAttribute("content", "index, follow");
    document.head.appendChild(existing);

    const { unmount } = render(
      <MemoryRouter>
        <NotFound />
      </MemoryRouter>,
    );
    expect(
      document.querySelector('meta[name="robots"]').getAttribute("content"),
    ).toBe("noindex");

    unmount();
    expect(
      document.querySelector('meta[name="robots"]').getAttribute("content"),
    ).toBe("index, follow");
  });
});
