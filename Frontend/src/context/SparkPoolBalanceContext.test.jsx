import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup, act } from "@testing-library/react";
import {
  SparkPoolBalanceProvider,
  useSparkPoolBalanceContext,
} from "./SparkPoolBalanceContext";
import { API } from "../config/constants";

const authState = { isAuthenticated: false, isLoading: false, user: null };
let socketListener = null;

vi.mock("./AuthContext", () => ({ useAuth: () => authState }));
vi.mock("../hooks/useAppSocketTarget", () => ({
  useAppSocketTarget: () => ({ key: "test" }),
}));
vi.mock("../hooks/useWebSocket", () => ({
  useWebSocket: (_target, { onMessage }) => {
    socketListener = onMessage;
    return { isConnected: true, status: "open" };
  },
}));
vi.mock("../utils/session", () => ({
  ensureGuestSession: vi.fn(),
  resetGuestSession: vi.fn(),
}));
vi.mock("../services/apiClient", () => ({ apiFetch: vi.fn() }));

const { ensureGuestSession, resetGuestSession } =
  await import("../utils/session");
const { apiFetch } = await import("../services/apiClient");

const ok = (sparks) => ({
  ok: true,
  status: 200,
  json: async () => ({ status: "success", data: { type: "guest", sparks } }),
});

const unauthorized = () => ({
  ok: false,
  status: 401,
  json: async () => ({ status: "fail", data: "guest_session_required" }),
});

function Probe() {
  const { balance } = useSparkPoolBalanceContext();
  return <span data-testid="balance">{balance === null ? "…" : balance}</span>;
}

// A fresh element every time: React skips re-rendering a subtree handed the
// identical element, which would hide the auth change under test.
const tree = () => (
  <SparkPoolBalanceProvider>
    <Probe />
  </SparkPoolBalanceProvider>
);

async function renderProvider() {
  let result;
  await act(async () => {
    result = render(tree());
  });
  return result;
}

const shown = () => screen.getByTestId("balance").textContent;

describe("SparkPoolBalanceContext", () => {
  beforeEach(() => {
    localStorage.clear();
    authState.isAuthenticated = false;
    authState.isLoading = false;
    authState.user = null;
    socketListener = null;
    ensureGuestSession.mockReset().mockResolvedValue(undefined);
    resetGuestSession.mockReset();
    apiFetch.mockReset().mockResolvedValue(ok(510));
  });

  afterEach(cleanup);

  it("asks for the guest balance at the guest-aware endpoint", async () => {
    await renderProvider();

    expect(apiFetch).toHaveBeenCalledWith(
      API.SPARK_POOL.BALANCE,
      expect.objectContaining({ method: "GET", skipAuthHeader: true }),
    );
    expect(shown()).toBe("510");
  });

  it("mints the credential cookie before asking", async () => {
    const order = [];
    ensureGuestSession.mockImplementation(async () => order.push("ensure"));
    apiFetch.mockImplementation(async () => {
      order.push("fetch");
      return ok(510);
    });

    await renderProvider();

    expect(order).toEqual(["ensure", "fetch"]);
  });

  it("shows nothing rather than zero until the server answers", async () => {
    let release;
    apiFetch.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve(ok(510));
      }),
    );

    await renderProvider();
    expect(shown()).toBe("…");

    await act(async () => release());
    expect(shown()).toBe("510");
  });

  it("does not render a cached zero left behind by a failed read", async () => {
    localStorage.setItem("guest_sparks_cache", "0");
    apiFetch.mockReturnValue(new Promise(() => {}));

    await renderProvider();

    expect(shown()).toBe("…");
  });

  it("shows a real cached balance while the request is in flight", async () => {
    localStorage.setItem("guest_sparks_cache", "300");
    apiFetch.mockReturnValue(new Promise(() => {}));

    await renderProvider();

    expect(shown()).toBe("300");
  });

  it("re-mints the cookie and retries once after a 401", async () => {
    apiFetch
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(ok(510));

    await renderProvider();

    expect(resetGuestSession).toHaveBeenCalledTimes(1);
    expect(ensureGuestSession).toHaveBeenCalledTimes(2);
    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(shown()).toBe("510");
  });

  it("stops after the second 401 rather than looping", async () => {
    apiFetch.mockResolvedValue(unauthorized());

    await renderProvider();

    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(shown()).toBe("…");
  });

  it("retries a failed read when the tab comes back", async () => {
    apiFetch.mockRejectedValueOnce(new Error("offline"));
    vi.spyOn(console, "warn").mockImplementation(() => {});

    await renderProvider();
    expect(shown()).toBe("…");

    apiFetch.mockResolvedValue(ok(450));
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(shown()).toBe("450");
  });

  it("does not re-ask once the balance is known", async () => {
    await renderProvider();

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it("takes the balance the socket pushes on connect", async () => {
    apiFetch.mockReturnValue(new Promise(() => {}));
    await renderProvider();

    await act(async () => {
      socketListener({ type: "spark_pool_balance", sparks: 480 });
    });

    expect(shown()).toBe("480");
    expect(localStorage.getItem("guest_sparks_cache")).toBe("480");
  });

  it("ignores socket traffic that is not a balance", async () => {
    await renderProvider();

    await act(async () => {
      socketListener({ type: "unread_update", total_unread: 3 });
    });

    expect(shown()).toBe("510");
  });

  it("drops the guest cache when a guest signs in", async () => {
    const { rerender } = await renderProvider();
    expect(localStorage.getItem("guest_sparks_cache")).toBe("510");

    apiFetch.mockResolvedValue(ok(2000));
    authState.isAuthenticated = true;
    authState.user = { user_id: "u1" };
    await act(async () => rerender(tree()));

    expect(localStorage.getItem("guest_sparks_cache")).toBeNull();
    expect(shown()).toBe("2000");
  });

  it("does not show one account's balance to the next", async () => {
    authState.isAuthenticated = true;
    authState.user = { user_id: "u1" };
    apiFetch.mockResolvedValue(ok(2000));

    const { rerender } = await renderProvider();
    expect(shown()).toBe("2000");

    apiFetch.mockReturnValue(new Promise(() => {}));
    authState.user = { user_id: "u2" };
    await act(async () => rerender(tree()));

    expect(shown()).toBe("…");
    expect(localStorage.getItem("sparks_cache")).toBeNull();
  });

  it("waits for the auth state before asking anyone for a balance", async () => {
    authState.isLoading = true;

    await renderProvider();

    expect(apiFetch).not.toHaveBeenCalled();
    expect(shown()).toBe("…");
  });
});
