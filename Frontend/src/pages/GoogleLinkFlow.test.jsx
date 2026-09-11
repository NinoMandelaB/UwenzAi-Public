import { StrictMode, useLayoutEffect } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Link, MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "../context/AuthContext";
import PublicOnlyRoute from "../components/PublicOnlyRoute";
import GoogleConnectionSection from "../components/GoogleConnectionSection";
import GoogleSignInButton from "../components/GoogleSignInButton";
import { API, ENV, STORAGE_KEYS } from "../config/constants";
import { clearCsrfCache, getAccessToken } from "../services/apiClient";
import Login from "./Login";
import Register from "./Register";
import { translations } from "../i18n/translations";

vi.mock("react-google-recaptcha", () => ({
  default: ({ onChange, ref }) => {
    useLayoutEffect(() => {
      ref.current = { reset: vi.fn() };
    }, [ref]);
    return (
      <button type="button" onClick={() => onChange("dummy-captcha")}>
        Verify test captcha
      </button>
    );
  },
}));
vi.mock("../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: true }),
}));
const t = (key) =>
  key.split(".").reduce((value, part) => value?.[part], translations.en) || key;
vi.mock("../context/LanguageContext", () => ({
  useLanguage: () => ({ t, language: "en" }),
}));
vi.mock("../utils/session", () => ({
  ensureGuestSession: vi.fn(),
  resetGuestSession: vi.fn(),
}));
vi.mock("../services/pushService", () => ({
  reconcilePushSubscription: vi.fn().mockResolvedValue(null),
  unsubscribePushLocally: vi.fn().mockResolvedValue(null),
  unsubscribeFromPush: vi.fn().mockResolvedValue(null),
}));

const A = {
  user_id: "A",
  username: "alice",
  email: "alice@example.test",
  age_verified: true,
};
const B = {
  user_id: "B",
  username: "bob",
  email: "bob@example.test",
  age_verified: true,
};
const success = (user = A) =>
  new Response(
    JSON.stringify({
      status: "success",
      data: { user, access_token: `${user.user_id}-token` },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
const linkSuccess = (user = A, linked = true) =>
  new Response(
    JSON.stringify({
      status: "success",
      data: {
        message: "google_link_successful",
        linked,
        user: { user_id: user.user_id },
      },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
const failure = (code, status = 400) =>
  new Response(JSON.stringify({ status: "fail", data: code }), {
    status,
    headers: { "content-type": "application/json" },
  });
const token = (email = A.email, exp = Math.floor(Date.now() / 1000) + 600) =>
  `dummy.${btoa(JSON.stringify({ email, exp, sub: "dummy-sub" }))}.signature`;
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
let auth;
let googleCallback;
let selectedCredential;
let googleReply;
let passwordReply;
let requests;
function Frame() {
  const value = useAuth();
  useLayoutEffect(() => {
    auth = value;
  }, [value]);
  return (
    <>
      <div data-testid="account">{value.user?.user_id || "guest"}</div>
      <Link to="/settings">Settings</Link>
      <Link to="/login">Login page</Link>
      <Outlet />
    </>
  );
}
function tree(path = "/login") {
  return (
    <StrictMode>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route element={<Frame />}>
              <Route element={<PublicOnlyRoute />}>
                <Route path="/login" element={<Login />} />
                <Route path="/register" element={<Register />} />
              </Route>
              <Route path="/settings" element={<GoogleConnectionSection />} />
              <Route path="/characters" element={<p>Character directory</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </StrictMode>
  );
}
const googleRequests = () => requests.filter((r) => r.path === API.AUTH.GOOGLE);
const choose = async () => {
  fireEvent.click(
    await screen.findByRole("button", { name: "Choose Google account" }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Continue with Google" }),
  );
};
const submitPassword = async () => {
  fireEvent.change(await screen.findByLabelText("Username"), {
    target: { value: "alice" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "dummy-password-only" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Verify test captcha" }));
  fireEvent.click(screen.getByRole("button", { name: "Log In", exact: true }));
};
const passwordLogin = async () => {
  await submitPassword();
  await waitFor(() =>
    expect(screen.getByTestId("account").textContent).toBe("A"),
  );
};
const signedInSettings = async () => {
  render(tree("/settings"));
  await act(async () => auth.login({ user: A, accessToken: "A-token" }));
  await screen.findByRole("button", { name: "Choose Google account" });
};

beforeEach(() => {
  vi.stubEnv("VITE_RECAPTCHA_SITE_KEY", "dummy-recaptcha");
  ENV.GOOGLE_CLIENT_ID = "dummy-google-client";
  localStorage.clear();
  sessionStorage.clear();
  clearCsrfCache();
  requests = [];
  selectedCredential = token();
  googleReply = ({ body }) => (body.confirm_link ? linkSuccess() : success());
  passwordReply = () => success();
  window.google = {
    accounts: {
      id: {
        initialize: ({ callback }) => {
          googleCallback = callback;
        },
        renderButton: (container) => {
          const callback = googleCallback;
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = "Continue with Google";
          button.onclick = () => callback({ credential: selectedCredential });
          container.appendChild(button);
        },
      },
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options = {}) => {
      const path = new URL(url).pathname;
      const request = {
        path,
        ...options,
        body: options.body ? JSON.parse(options.body) : undefined,
      };
      requests.push(request);
      if (path === API.AUTH.CSRF)
        return new Response(
          JSON.stringify({ data: { csrfToken: "dummy-csrf" } }),
        );
      if (path === API.AUTH.SIGNIN) return passwordReply(request);
      if (path === API.AUTH.GOOGLE) return googleReply(request);
      throw new Error(`Unexpected mock endpoint: ${path}`);
    }),
  );
});
afterEach(() => {
  // No Google credential may appear in browser storage, including successful flows.
  expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(
    "signature",
  );
  cleanup();
  delete window.google;
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("Google login and explicit password-to-connection flow", () => {
  it("removes Google only from signup, retaining manual signup and login link", async () => {
    render(tree("/register"));
    await screen.findByRole("button", { name: /Sign up/i, exact: true });
    expect(
      screen.queryByRole("button", { name: "Continue with Google" }),
    ).toBeNull();
    expect(
      screen.getByRole("link", { name: "Log In", exact: true }),
    ).toBeTruthy();
    expect(googleRequests()).toHaveLength(0);
  });

  it("retains ordinary Google login with no confirmation or signup payload", async () => {
    render(tree());
    fireEvent.click(
      await screen.findByRole("button", { name: "Continue with Google" }),
    );
    await screen.findByText("Character directory");
    expect(googleRequests()[0].body).toEqual({
      credential: selectedCredential,
    });
    expect(googleRequests()[0].headers.Authorization).toBeUndefined();
    expect(screen.getByTestId("account").textContent).toBe("A");
  });

  it("walks Google attempt → password login → fresh selection → explicit bearer confirmation", async () => {
    googleReply = ({ body }) =>
      body.confirm_link
        ? linkSuccess()
        : failure("google_link_confirmation_required");
    render(tree());
    fireEvent.click(
      await screen.findByRole("button", { name: "Continue with Google" }),
    );
    await screen.findByText(/Nothing is linked automatically/);
    await passwordLogin();
    await screen.findByText("Connect Google");
    expect(googleRequests()).toHaveLength(1);
    selectedCredential = token(A.email.toUpperCase());
    await choose();
    expect(googleRequests()).toHaveLength(1);
    expect(
      screen.getByText(/Allow future Google sign-ins/).textContent,
    ).toContain(A.email);
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm Google connection" }),
    );
    await screen.findByText(/Google is connected/);
    expect(googleRequests()).toHaveLength(2);
    expect(googleRequests()[1].body).toEqual({
      credential: selectedCredential,
      confirm_link: true,
    });
    expect(googleRequests()[1].headers.Authorization).toBe("Bearer A-token");
    expect(googleRequests()[1].headers["X-CSRFToken"]).toBe("dummy-csrf");
    expect(getAccessToken()).toBe("A-token");
  });

  it("explains and retains the no-Google-signup policy", async () => {
    googleReply = () => failure("google_signup_not_allowed");
    render(tree());
    fireEvent.click(
      await screen.findByRole("button", { name: "Continue with Google" }),
    );
    await screen.findByText(/You cannot create an account using Google/);
    expect(screen.getByTestId("account").textContent).toBe("guest");
  });

  it("blocks a different selected Google email without sending a link", async () => {
    await signedInSettings();
    selectedCredential = token(B.email);
    await choose();
    await screen.findByText(/No connection was made/);
    expect(
      screen.queryByRole("button", { name: "Confirm Google connection" }),
    ).toBeNull();
    expect(googleRequests()).toHaveLength(0);
  });

  it("cancel discards selection and stale Google chooser callbacks", async () => {
    await signedInSettings();
    fireEvent.click(
      screen.getByRole("button", { name: "Choose Google account" }),
    );
    const staleCallback = googleCallback;
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => staleCallback({ credential: selectedCredential }));
    expect(
      screen.queryByRole("button", { name: "Confirm Google connection" }),
    ).toBeNull();
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(googleRequests()).toHaveLength(0);
    expect(screen.getByTestId("account").textContent).toBe("A");
  });

  it("rejects expired selections without a request", async () => {
    await signedInSettings();
    selectedCredential = token(A.email, 1);
    await choose();
    await screen.findByText(/invalid or expired/);
    expect(googleRequests()).toHaveLength(0);
  });

  it("rejects a selection that expires before confirmation", async () => {
    await signedInSettings();
    await choose();
    const now = vi.spyOn(Date, "now").mockReturnValue(Date.now() + 700_000);
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm Google connection" }),
    );
    await screen.findByText(/invalid or expired/);
    expect(googleRequests()).toHaveLength(0);
    now.mockRestore();
  });

  it("retires an unconfirmed selection after another account signs in", async () => {
    await signedInSettings();
    await choose();
    await act(async () => auth.login({ user: B, accessToken: "B-token" }));
    expect(
      screen.queryByRole("button", { name: "Confirm Google connection" }),
    ).toBeNull();
    expect(screen.getByText("bob")).toBeTruthy();
    expect(googleRequests()).toHaveLength(0);
  });

  it.each(["switch", "same-account-new-session", "logout"])(
    "ignores late confirmation after %s",
    async (boundary) => {
      const pending = deferred();
      googleReply = () => pending.promise;
      await signedInSettings();
      await choose();
      fireEvent.click(
        screen.getByRole("button", { name: "Confirm Google connection" }),
      );
      await waitFor(() => expect(googleRequests()).toHaveLength(1));
      const expected = boundary === "switch" ? B : A;
      await act(async () =>
        boundary === "logout"
          ? auth.clearAuthState()
          : auth.login({
              user: expected,
              accessToken: `${expected.user_id}-new-token`,
            }),
      );
      await act(async () => pending.resolve(linkSuccess()));
      expect(googleRequests()[0].signal.aborted).toBe(true);
      expect(screen.queryByText(/Google is connected/)).toBeNull();
      expect(screen.getByTestId("account").textContent).toBe(
        boundary === "logout" ? "guest" : expected.user_id,
      );
      expect(getAccessToken()).toBe(
        boundary === "logout" ? null : `${expected.user_id}-new-token`,
      );
    },
  );

  it("rejects a server response for a different user rather than switching accounts", async () => {
    googleReply = () => linkSuccess(B);
    await signedInSettings();
    await choose();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm Google connection" }),
    );
    await screen.findByText(/Nothing is linked automatically/);
    expect(getAccessToken()).toBe("A-token");
    expect(
      JSON.parse(localStorage.getItem(STORAGE_KEYS.AUTH_USER)).user_id,
    ).toBe("A");
  });

  it("does not restore a late password-login response over another account", async () => {
    const pending = deferred();
    passwordReply = () => pending.promise;
    render(tree());
    fireEvent.change(await screen.findByLabelText("Username"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "dummy-password" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Verify test captcha" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Log In", exact: true }),
    );
    await waitFor(() =>
      expect(requests.some((r) => r.path === API.AUTH.SIGNIN)).toBe(true),
    );
    await act(async () => auth.login({ user: B, accessToken: "B-token" }));
    await act(async () => pending.resolve(success()));
    expect(getAccessToken()).toBe("B-token");
    expect(
      JSON.parse(localStorage.getItem(STORAGE_KEYS.AUTH_USER)).user_id,
    ).toBe("B");
  });

  it("accepts pure link success without login tokens and preserves the entire profile", async () => {
    await signedInSettings();
    const profile = localStorage.getItem(STORAGE_KEYS.AUTH_USER);
    await choose();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm Google connection" }),
    );
    await screen.findByText(/Google is connected/);
    expect(getAccessToken()).toBe("A-token");
    expect(localStorage.getItem(STORAGE_KEYS.AUTH_USER)).toBe(profile);
  });

  it("rejects an unacknowledged link without changing authentication", async () => {
    googleReply = () => linkSuccess(A, false);
    await signedInSettings();
    await choose();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm Google connection" }),
    );
    await screen.findByRole("alert");
    expect(screen.queryByText(/Google is connected/)).toBeNull();
    expect(getAccessToken()).toBe("A-token");
  });

  const expireLink = async () => {
    googleReply = () => failure("token_not_valid", 401);
    await signedInSettings();
    await choose();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm Google connection" }),
    );
    await screen.findByRole("button", {
      name: "Sign in again with password",
    });
  };

  it("offers explicit password reauthentication then fresh confirmation, without refresh replay", async () => {
    await expireLink();
    expect(googleRequests()).toHaveLength(1);
    expect(requests.some((r) => r.path === API.AUTH.REFRESH)).toBe(false);
    fireEvent.click(
      screen.getByRole("button", { name: "Sign in again with password" }),
    );
    expect(getAccessToken()).toBeNull();
    await passwordLogin();
    await screen.findByText("Connect Google");
    expect(googleRequests()).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "Confirm Google connection" }),
    ).toBeNull();
    googleReply = () => linkSuccess();
    await choose();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm Google connection" }),
    );
    await screen.findByText(/Google is connected/);
    expect(googleRequests()).toHaveLength(2);
    expect(googleRequests()[1].headers.Authorization).toBe("Bearer A-token");
  });

  it("retires the password reauthentication action when another account signs in", async () => {
    await expireLink();
    const staleButton = screen.getByRole("button", {
      name: "Sign in again with password",
    });
    await act(async () => auth.login({ user: B, accessToken: "B-token" }));
    fireEvent.click(staleButton);
    expect(getAccessToken()).toBe("B-token");
    expect(
      screen.queryByRole("button", { name: "Sign in again with password" }),
    ).toBeNull();
    expect(screen.queryByLabelText("Password")).toBeNull();
  });

  it.each(["unmount", "switch"])(
    "ignores a late password reauthentication response after %s",
    async (boundary) => {
      await expireLink();
      fireEvent.click(
        screen.getByRole("button", { name: "Sign in again with password" }),
      );
      const pending = deferred();
      passwordReply = () => pending.promise;
      await submitPassword();
      await waitFor(() =>
        expect(requests.some((r) => r.path === API.AUTH.SIGNIN)).toBe(true),
      );
      if (boundary === "unmount") cleanup();
      else
        await act(async () => auth.login({ user: B, accessToken: "B-token" }));
      await act(async () => pending.resolve(success()));
      expect(getAccessToken()).toBe(boundary === "unmount" ? null : "B-token");
      expect(googleRequests()).toHaveLength(1);
      expect(
        requests.find((r) => r.path === API.AUTH.SIGNIN).signal.aborted,
      ).toBe(true);
    },
  );

  it("ignores disabled Google callbacks and late normal Google responses after unmount", async () => {
    const pending = deferred();
    googleReply = () => pending.promise;
    const onSuccess = vi.fn();
    const view = render(<GoogleSignInButton disabled onSuccess={onSuccess} />);
    await act(async () => googleCallback({ credential: selectedCredential }));
    expect(googleRequests()).toHaveLength(0);
    view.rerender(<GoogleSignInButton onSuccess={onSuccess} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Continue with Google" }),
    );
    await waitFor(() => expect(googleRequests()).toHaveLength(1));
    view.unmount();
    await act(async () => pending.resolve(success()));
    expect(onSuccess).not.toHaveBeenCalled();
    expect(localStorage.getItem(STORAGE_KEYS.AUTH_USER)).toBeNull();
  });
});
