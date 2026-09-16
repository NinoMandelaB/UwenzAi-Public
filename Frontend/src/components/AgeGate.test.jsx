// src/components/AgeGate.test.jsx
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgeGate from "./AgeGate";

let scope = { isAuthenticated: false, isLoading: false };
vi.mock("../context/AuthContext", () => ({ useAuth: () => scope }));
const t = (key) => key;
vi.mock("../context/LanguageContext", () => ({ useLanguage: () => ({ t }) }));

const Gate = ({ initialEntry }) => (
  <MemoryRouter initialEntries={[initialEntry]}>
    <AgeGate>
      <Routes>
        <Route path="/terms" element={<div>TERMS PAGE</div>} />
        <Route path="/privacy" element={<div>PRIVACY PAGE</div>} />
        <Route path="/characters" element={<div>APP PAGE</div>} />
      </Routes>
    </AgeGate>
  </MemoryRouter>
);

beforeEach(() => {
  scope = { isAuthenticated: false, isLoading: false };
  sessionStorage.clear();
});

afterEach(cleanup);

describe("AgeGate", () => {
  it("shows the 18+ gate to unconfirmed guests on app routes", () => {
    render(<Gate initialEntry="/characters" />);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("links the legal line to Terms of Service and Privacy Policy", () => {
    render(<Gate initialEntry="/characters" />);
    const terms = screen.getByRole("link", { name: "ageGate.termsLink" });
    const privacy = screen.getByRole("link", { name: "ageGate.privacyLink" });
    expect(terms.getAttribute("href")).toBe("/terms");
    expect(privacy.getAttribute("href")).toBe("/privacy");
  });

  it("lets guests read legal pages without passing the gate", () => {
    render(<Gate initialEntry="/terms" />);
    expect(screen.getByText("TERMS PAGE")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();

    sessionStorage.clear();
    cleanup();
    render(<Gate initialEntry="/privacy" />);
    expect(screen.getByText("PRIVACY PAGE")).toBeTruthy();
  });

  it("hides the gate for authenticated users", () => {
    scope.isAuthenticated = true;
    render(<Gate initialEntry="/characters" />);
    expect(screen.getByText("APP PAGE")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
