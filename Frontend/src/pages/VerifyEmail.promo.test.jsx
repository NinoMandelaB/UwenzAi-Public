// src/pages/VerifyEmail.promo.test.jsx
// The promo outcome from signup is shown on the verification screen: an
// applied code shows the credited Sparks; a rejected code shows a notice that
// the account was still created with the standard signup bonus.

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import VerifyEmail from "./VerifyEmail";

const translations = {
  "auth.promoApplied":
    "Promo code applied — {sparks} bonus sparks were added to your account.",
  "auth.promoNotApplied":
    "Your promo code could not be applied (it may be invalid or already used). Your account was still created with the standard signup bonus.",
};
const t = (key, vars = {}) => {
  let value = translations[key] || key;
  Object.entries(vars).forEach(([k, v]) => {
    value = value.replace(`{${k}}`, String(v));
  });
  return value;
};
vi.mock("../context/LanguageContext", () => ({ useLanguage: () => ({ t }) }));
vi.mock("../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));
vi.mock("../services/authService", () => ({
  verifyEmailCode: vi.fn(),
  resendVerificationCode: vi.fn(),
}));

function renderWithState(promo) {
  return render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/verify-email",
          state: { user_id: "u-1", email: "someone@example.com", promo },
        },
      ]}
    >
      <VerifyEmail />
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("VerifyEmail promo outcome banner", () => {
  it("shows the credited Sparks when the code was applied", () => {
    renderWithState({ status: "applied", sparks: 500 });
    expect(
      screen.getByText(/Promo code applied — 500 bonus sparks/),
    ).toBeTruthy();
  });

  it("shows a non-blocking notice when the code was not applied", () => {
    renderWithState({ status: "not_applied", reason: "already_redeemed" });
    expect(
      screen.getByText(
        /could not be applied \(it may be invalid or already used\)/,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(/still created with the standard signup bonus/),
    ).toBeTruthy();
  });

  it("renders no banner when no code was submitted", () => {
    renderWithState(undefined);
    expect(screen.queryByText(/Promo code applied/)).toBeNull();
    expect(screen.queryByText(/could not be applied/)).toBeNull();
  });
});
