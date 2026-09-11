import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "./AuthContext";
import FeatureTogglesSection from "../components/FeatureTogglesSection";
import { ChatEnhancementControls } from "../components/ChatModal";
import { API, STORAGE_KEYS } from "../config/constants";
import {
  apiFetch,
  getAccessToken,
  refreshAccessToken,
  setAccessToken,
} from "../services/apiClient";
import { CLEANUP_TIMEOUT_MS } from "../utils/boundedTask";
import { useLayoutEffect } from "react";

vi.mock("../services/apiClient", async (original) => ({
  ...(await original()),
  apiFetch: vi.fn(),
  refreshAccessToken: vi.fn(),
}));
vi.mock("../utils/session", () => ({
  ensureGuestSession: vi.fn().mockResolvedValue(null),
  resetGuestSession: vi.fn(),
}));
const t = (key) => key;
vi.mock("./LanguageContext", () => ({ useLanguage: () => ({ t }) }));

const A = {
  user_id: "A",
  name: "Account A",
  feature_preferences: { reminders: false, websearch: false },
};
const B = {
  user_id: "B",
  name: "Account B",
  feature_preferences: { reminders: false, websearch: false },
};
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
const response = (data = {}) => ({
  ok: true,
  status: 200,
  json: async () => ({ status: "success", data }),
});
let auth;
let subscription;
let remoteCleanup;
function Probe({ settings = true }) {
  const value = useAuth();
  useLayoutEffect(() => {
    auth = value;
  }, [value]);
  return (
    <>
      <div data-testid="identity">
        {value.user?.user_id || "guest"}:{String(value.isAuthenticated)}
      </div>
      {settings && value.isAuthenticated && <FeatureTogglesSection />}
      <ChatEnhancementControls
        activeConvId="conv"
        canUseConversationEnhancements={value.isAuthenticated}
        showWebSearch={Boolean(value.user?.feature_preferences?.websearch)}
        showDeepThink={false}
        onToggleWebSearch={() => {}}
      />
    </>
  );
}
const tree = (settings = true) => (
  <AuthProvider>
    <Probe settings={settings} />
  </AuthProvider>
);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem(STORAGE_KEYS.AUTH_USER, JSON.stringify(A));
  remoteCleanup = null;
  subscription = {
    endpoint: "https://push.example/A",
    toJSON: () => ({ endpoint: "https://push.example/A", keys: {} }),
    unsubscribe: vi.fn().mockResolvedValue(true),
  };
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {
      getRegistration: vi.fn().mockResolvedValue({
        pushManager: { getSubscription: async () => subscription },
      }),
    },
  });
  Object.defineProperty(window, "PushManager", {
    configurable: true,
    value: class {},
  });
  Object.defineProperty(window, "Notification", {
    configurable: true,
    value: { permission: "granted" },
  });
  refreshAccessToken.mockImplementation(async () => {
    setAccessToken("A-token");
    return { user: A, accessToken: "A-token" };
  });
  apiFetch.mockImplementation(async (path) => {
    if (path === API.AUTH.FEATURE_PREFERENCES)
      return response({ feature_preferences: A.feature_preferences });
    if (
      [API.AUTH.SIGNOUT, API.PUSH.UNSUBSCRIBE].includes(path) &&
      remoteCleanup
    )
      return remoteCleanup.promise;
    return response();
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("real AuthProvider and Settings session fencing", () => {
  it("shares preference GET state with actual chat controls", async () => {
    apiFetch.mockImplementation(async (path) =>
      path === API.AUTH.FEATURE_PREFERENCES
        ? response({
            feature_preferences: { websearch: true, reminders: true },
          })
        : response(),
    );
    render(tree());
    expect(await screen.findByTestId("web-search-toggle")).toBeTruthy();
    expect(
      screen
        .getByLabelText("settings.feature_websearch")
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      JSON.parse(localStorage.getItem(STORAGE_KEYS.AUTH_USER))
        .feature_preferences.websearch,
    ).toBe(true);
  });

  it.each(["GET", "PATCH"])(
    "ignores late %s after Settings unmount",
    async (method) => {
      const pending = deferred();
      apiFetch.mockImplementation(async (path, options = {}) => {
        if (path === API.AUTH.FEATURE_PREFERENCES && options.method === method)
          return pending.promise;
        return response({ feature_preferences: A.feature_preferences });
      });
      const view = render(tree());
      await waitFor(() => expect(auth.isAuthenticated).toBe(true));
      if (method === "PATCH") {
        const button = await screen.findByLabelText(
          "settings.feature_websearch",
        );
        await waitFor(() => expect(button.disabled).toBe(false));
        fireEvent.click(button);
      }
      view.rerender(tree(false));
      await act(async () =>
        pending.resolve(response({ feature_preferences: { websearch: true } })),
      );
      expect(auth.user.feature_preferences.websearch).toBe(false);
    },
  );

  it.each([
    ["GET", B],
    ["PATCH", B],
    ["GET", A],
    ["PATCH", A],
  ])(
    "ignores delayed %s across logout and a new session for %j",
    async (method, nextUser) => {
      const pending = deferred();
      let hold = true;
      apiFetch.mockImplementation(async (path, options = {}) => {
        if (
          path === API.AUTH.FEATURE_PREFERENCES &&
          options.method === method &&
          hold
        )
          return pending.promise;
        return response({ feature_preferences: nextUser.feature_preferences });
      });
      render(tree());
      await waitFor(() => expect(auth.isAuthenticated).toBe(true));
      if (method === "PATCH") {
        const toggle = await screen.findByLabelText(
          "settings.feature_websearch",
        );
        await waitFor(() => expect(toggle.disabled).toBe(false));
        fireEvent.click(toggle);
      }
      const oldGeneration = auth.sessionGeneration;
      hold = false;
      await act(async () => {
        await auth.logout();
      });
      expect(screen.getByTestId("identity").textContent).toBe("guest:false");
      await act(async () =>
        auth.login({ user: nextUser, accessToken: `${nextUser.user_id}-new` }),
      );
      await act(async () =>
        pending.resolve(response({ feature_preferences: { websearch: true } })),
      );
      expect(auth.user.user_id).toBe(nextUser.user_id);
      expect(auth.user.feature_preferences.websearch).toBe(false);
      expect(auth.sessionGeneration).toBeGreaterThan(oldGeneration);
      expect(getAccessToken()).toBe(`${nextUser.user_id}-new`);
      expect(
        JSON.parse(localStorage.getItem(STORAGE_KEYS.AUTH_USER)).user_id,
      ).toBe(nextUser.user_id);
    },
  );

  it.each([null, { user: A }])(
    "retires local push on confirmed startup session loss (%j)",
    async (refreshed) => {
      refreshAccessToken.mockResolvedValue(refreshed);
      render(tree());
      await waitFor(() => expect(auth.isLoading).toBe(false));
      expect(auth.isAuthenticated).toBe(false);
      await waitFor(() => expect(subscription.unsubscribe).toHaveBeenCalled());
    },
  );

  it("does not let a stale profile callback or preference PATCH restore a logged-out user", async () => {
    render(tree());
    await waitFor(() => expect(auth.isAuthenticated).toBe(true));
    const oldSetUser = auth.setUser;
    const pending = deferred();
    apiFetch.mockImplementation(async (path, options = {}) =>
      path === API.AUTH.FEATURE_PREFERENCES && options.method === "PATCH"
        ? pending.promise
        : response(),
    );
    const button = screen.getByLabelText("settings.feature_websearch");
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await act(async () => {
      await auth.logout();
    });
    await act(async () => {
      pending.resolve(response({ feature_preferences: { websearch: true } }));
      oldSetUser({ ...A, name: "Old profile save" });
    });
    expect(screen.getByTestId("identity").textContent).toBe("guest:false");
    expect(getAccessToken()).toBe(null);
    expect(localStorage.getItem(STORAGE_KEYS.AUTH_USER)).toBe(null);
  });

  it("does not let an old profile body parsed after A logs back in overwrite the new session", async () => {
    render(tree(false));
    await waitFor(() => expect(auth.isAuthenticated).toBe(true));
    const body = deferred();
    apiFetch.mockImplementation(async (path) =>
      path === API.AUTH.ME
        ? { ok: true, status: 200, json: () => body.promise }
        : response(),
    );
    let refreshing;
    await act(async () => {
      refreshing = auth.refreshCurrentUser();
    });
    await act(async () => {
      await auth.logout();
      auth.login({ user: { ...A, name: "Fresh A" }, accessToken: "fresh-A" });
    });
    await act(async () => {
      body.resolve({ status: "success", data: { user: A } });
      await refreshing;
    });
    expect(auth.user.name).toBe("Fresh A");
    expect(getAccessToken()).toBe("fresh-A");
  });

  it("does not destructively unsubscribe on an offline refresh", async () => {
    refreshAccessToken.mockRejectedValue(new TypeError("offline"));
    render(tree());
    await waitFor(() => expect(auth.isLoading).toBe(false));
    expect(auth.isAuthenticated).toBe(false);
    expect(subscription.unsubscribe).not.toHaveBeenCalled();
    expect(localStorage.getItem(STORAGE_KEYS.AUTH_USER)).not.toBe(null);
  });

  it("clears local auth immediately when both cleanup requests never settle", async () => {
    render(tree());
    await waitFor(() => expect(auth.isAuthenticated).toBe(true));
    remoteCleanup = deferred();
    vi.useFakeTimers();
    let loggingOut;
    await act(async () => {
      loggingOut = auth.logout();
    });
    expect(auth.isAuthenticated).toBe(false);
    expect(getAccessToken()).toBe(null);
    expect(localStorage.getItem(STORAGE_KEYS.AUTH_USER)).toBe(null);
    expect(subscription.unsubscribe).toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CLEANUP_TIMEOUT_MS + 1);
      await loggingOut;
    });
    expect(
      apiFetch.mock.calls.filter(
        ([path]) => path === API.PUSH.UNSUBSCRIBE,
      )[0][1],
    ).toMatchObject({ authToken: "A-token", skipAuthRefresh: true });
  });

  const jwtWithExp = (exp) =>
    `h.${btoa(JSON.stringify({ exp })).replace(/=+$/, "")}.s`;

  it("renews a verifiably expired token before sign-out so the cookie is revoked and push is removed", async () => {
    const expired = jwtWithExp(Math.floor(Date.now() / 1000) - 60);
    const renewed = jwtWithExp(Math.floor(Date.now() / 1000) + 900);
    refreshAccessToken.mockImplementation(async () => {
      setAccessToken(renewed);
      return { user: A, accessToken: renewed };
    });
    render(tree(false));
    await waitFor(() => expect(auth.isAuthenticated).toBe(true));
    setAccessToken(expired);
    refreshAccessToken.mockClear();
    await act(async () => {
      await auth.logout();
    });
    expect(refreshAccessToken).toHaveBeenCalledTimes(1);
    expect(
      apiFetch.mock.calls.find(([path]) => path === API.AUTH.SIGNOUT)[1],
    ).toMatchObject({ authToken: renewed });
    expect(
      apiFetch.mock.calls.find(([path]) => path === API.PUSH.UNSUBSCRIBE)[1],
    ).toMatchObject({ authToken: renewed });
    expect(getAccessToken()).toBe(null);
    expect(auth.isAuthenticated).toBe(false);
  });

  it("retries sign-out without a bearer when the API rejects the departing token", async () => {
    render(tree(false));
    await waitFor(() => expect(auth.isAuthenticated).toBe(true));
    apiFetch.mockImplementation(async (path, options = {}) => {
      if (path === API.AUTH.SIGNOUT && options.authToken)
        return { ok: false, status: 401, json: async () => ({}) };
      return response();
    });
    let result;
    await act(async () => {
      result = await auth.logout();
    });
    const signouts = apiFetch.mock.calls.filter(
      ([path]) => path === API.AUTH.SIGNOUT,
    );
    expect(signouts).toHaveLength(2);
    expect(signouts[1][1]).toMatchObject({
      authToken: null,
      skipAuthHeader: true,
      skipAuthRefresh: true,
    });
    expect(result.remoteSignedOut).toBe(true);
  });

  it("does not renew or delay logout when the token is still valid", async () => {
    render(tree(false));
    await waitFor(() => expect(auth.isAuthenticated).toBe(true));
    setAccessToken(jwtWithExp(Math.floor(Date.now() / 1000) + 600));
    refreshAccessToken.mockClear();
    await act(async () => {
      await auth.logout();
    });
    expect(refreshAccessToken).not.toHaveBeenCalled();
  });

  it("does not erase the next account when old local and remote cleanup hang", async () => {
    render(tree(false));
    await waitFor(() => expect(auth.isAuthenticated).toBe(true));
    remoteCleanup = deferred();
    subscription.unsubscribe.mockReturnValue(new Promise(() => {}));
    vi.useFakeTimers();
    let loggingOut;
    await act(async () => {
      loggingOut = auth.logout();
    });
    expect(getAccessToken()).toBe(null);
    await act(async () => auth.login({ user: B, accessToken: "B-new" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CLEANUP_TIMEOUT_MS + 1);
      await loggingOut;
      remoteCleanup.resolve(response());
    });
    expect(auth.user.user_id).toBe("B");
    expect(getAccessToken()).toBe("B-new");
    expect(
      JSON.parse(localStorage.getItem(STORAGE_KEYS.AUTH_USER)).user_id,
    ).toBe("B");
  });
});
