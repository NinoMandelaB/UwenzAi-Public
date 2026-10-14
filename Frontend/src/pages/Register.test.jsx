// src/pages/Register.test.jsx
// Register page: the optional promo code field renders, and a submitted code
// is passed through to the signup payload (the backend applies it on top of
// the sign-up bonus and reports the outcome; VerifyEmail shows it).

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Register from "./Register";

const t = (key) => key;
vi.mock("../context/LanguageContext", () => ({ useLanguage: () => ({ t }) }));
vi.mock("../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));
vi.mock("../i18n/legal", () => ({ LEGAL_POLICY_VERSION: "test-v1" }));

// The real widget is a reCAPTCHA iframe; the test double immediately reports
// a solved token so validation can pass without network access.
vi.mock("react-google-recaptcha", async () => {
  const { useEffect } = await import("react");
  return {
    __esModule: true,
    default: function StubRecaptcha({ onChange }) {
      useEffect(() => {
        onChange("stub-captcha-token");
      }, []);
      return <div data-testid="recaptcha-stub" />;
    },
  };
});

// Captured payloads from the mocked signup call.
const signupCalls = [];
vi.mock("../services/authService", () => ({
  signupUser: vi.fn(async (payload) => {
    signupCalls.push(payload);
    return {
      status: "success",
      data: {
        message: "verification_email_sent",
        user: { user_id: "u-1", username: "promotester" },
        spark_pool: { sparks: 2500 },
        promo: { status: "applied", sparks: 500 },
      },
    };
  }),
}));

function fillForm() {
  fireEvent.change(screen.getByLabelText("Username"), {
    target: { value: "promotester" },
  });
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "promotester@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Screen Name"), {
    target: { value: "Promo Tester" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "pw123456" },
  });
  fireEvent.change(screen.getByLabelText("Confirm Password"), {
    target: { value: "pw123456" },
  });
  fireEvent.click(screen.getByText("Male"));
  fireEvent.click(screen.getByLabelText(/I confirm that I am 18/));
  fireEvent.click(screen.getByLabelText(/I have read and agree to the/));
  fireEvent.click(screen.getByLabelText(/I consent to the collection/));
}

beforeEach(() => {
  signupCalls.length = 0;
  vi.stubEnv("VITE_RECAPTCHA_SITE_KEY", "test-site-key");
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("Register promo code field", () => {
  it("renders the optional promo code input with an accessible label", () => {
    render(
      <MemoryRouter>
        <Register />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText("Promo code (optional)")).toBeTruthy();
  });

  it("sends the entered promo code with the signup payload", async () => {
    render(
      <MemoryRouter>
        <Register />
      </MemoryRouter>,
    );

    fillForm();
    fireEvent.change(screen.getByLabelText("Promo code (optional)"), {
      target: { value: " flyer-2026 " },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign up/i }));

    await waitFor(() => expect(signupCalls.length).toBe(1));
    // Trimmed; the backend normalises case and separators.
    expect(signupCalls[0].promo_code).toBe("flyer-2026");
    expect(signupCalls[0].username).toBe("promotester");
  });

  it("omits promo_code from the payload when the field is empty", async () => {
    render(
      <MemoryRouter>
        <Register />
      </MemoryRouter>,
    );

    fillForm();
    fireEvent.click(screen.getByRole("button", { name: /sign up/i }));

    await waitFor(() => expect(signupCalls.length).toBe(1));
    expect(signupCalls[0].promo_code).toBeUndefined();
  });
});
