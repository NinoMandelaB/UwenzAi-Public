import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup, act } from "@testing-library/react";
import { UnreadProvider, useUnread } from "./UnreadContext";

const authState = { isAuthenticated: true, isLoading: false };
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
vi.mock("../utils/session", () => ({ ensureGuestSession: vi.fn() }));
vi.mock("../services/conversationService", () => ({
  getUnreadSummary: vi.fn(),
  markConversationRead: vi.fn(),
}));

const { getUnreadSummary, markConversationRead } =
  await import("../services/conversationService");

function Probe() {
  const { totalUnread, unreadFor, seed, markRead } = useUnread();
  return (
    <div>
      <span data-testid="total">{totalUnread}</span>
      <span data-testid="a">{unreadFor("a")}</span>
      <button
        onClick={() =>
          seed([
            ["a", 2],
            ["b", 0],
            ["c", 1],
          ])
        }
      >
        seed
      </button>
      <button onClick={() => markRead("a")}>read</button>
    </div>
  );
}

// A fresh element every time: React skips re-rendering a subtree handed the
// identical element, which would hide the auth change this file is testing.
const tree = () => (
  <UnreadProvider>
    <Probe />
  </UnreadProvider>
);

async function renderProvider() {
  let result;
  await act(async () => {
    result = render(tree());
  });
  return result;
}

const click = async (name) => {
  await act(async () => {
    screen.getByText(name).click();
  });
};

describe("UnreadContext", () => {
  beforeEach(() => {
    authState.isAuthenticated = true;
    authState.isLoading = false;
    socketListener = null;
    getUnreadSummary.mockReset().mockResolvedValue({ conversations: [] });
    markConversationRead.mockReset().mockResolvedValue({});
  });

  afterEach(() => {
    cleanup();
    delete navigator.setAppBadge;
    delete navigator.clearAppBadge;
  });

  it("loads the summary on mount", async () => {
    getUnreadSummary.mockResolvedValue({
      conversations: [
        { conversation_id: "a", unread_count: 3 },
        { conversation_id: "b", unread_count: 4 },
      ],
    });

    await renderProvider();

    expect(screen.getByTestId("a").textContent).toBe("3");
    expect(screen.getByTestId("total").textContent).toBe("7");
  });

  it("adopts the counts the conversation list already fetched", async () => {
    await renderProvider();
    await click("seed");

    expect(screen.getByTestId("a").textContent).toBe("2");
    expect(screen.getByTestId("total").textContent).toBe("3");
  });

  it("replaces rather than merges when seeded again", async () => {
    getUnreadSummary.mockResolvedValue({
      conversations: [{ conversation_id: "gone", unread_count: 9 }],
    });

    await renderProvider();
    expect(screen.getByTestId("total").textContent).toBe("9");

    await click("seed");
    expect(screen.getByTestId("total").textContent).toBe("3");
  });

  it("applies a pushed count without refetching", async () => {
    await renderProvider();
    await click("seed");

    await act(async () => {
      socketListener({
        type: "unread_update",
        conversation_id: "a",
        unread_count: 5,
      });
    });

    expect(screen.getByTestId("a").textContent).toBe("5");
    expect(screen.getByTestId("total").textContent).toBe("6");
    expect(getUnreadSummary).toHaveBeenCalledTimes(1);
  });

  it("ignores socket traffic that is not an unread update", async () => {
    await renderProvider();
    await click("seed");

    await act(async () => {
      socketListener({ type: "spark_pool_balance", sparks: 12 });
    });

    expect(screen.getByTestId("total").textContent).toBe("3");
  });

  it("clears the badge before the server confirms", async () => {
    let resolveRequest;
    markConversationRead.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );

    await renderProvider();
    await click("seed");
    await click("read");

    expect(screen.getByTestId("a").textContent).toBe("0");
    expect(screen.getByTestId("total").textContent).toBe("1");
    expect(markConversationRead).toHaveBeenCalledWith("a");

    await act(async () => resolveRequest({}));
  });

  it("keeps the badge cleared when the mark-read request fails", async () => {
    markConversationRead.mockRejectedValue(new Error("offline"));
    vi.spyOn(console, "warn").mockImplementation(() => {});

    await renderProvider();
    await click("seed");
    await click("read");

    expect(screen.getByTestId("total").textContent).toBe("1");
  });

  it("mirrors the total onto the app-icon badge and clears it at zero", async () => {
    navigator.setAppBadge = vi.fn().mockResolvedValue(undefined);
    navigator.clearAppBadge = vi.fn().mockResolvedValue(undefined);
    getUnreadSummary.mockResolvedValue({
      conversations: [{ conversation_id: "a", unread_count: 3 }],
    });

    await renderProvider();

    expect(navigator.setAppBadge).toHaveBeenCalledWith(3);

    await act(async () => {
      socketListener({
        type: "unread_update",
        conversation_id: "a",
        unread_count: 0,
      });
    });

    expect(navigator.clearAppBadge).toHaveBeenCalled();
  });

  it("does not leave one owner's counts on screen after a sign-out", async () => {
    getUnreadSummary.mockResolvedValue({
      conversations: [{ conversation_id: "a", unread_count: 6 }],
    });

    const { rerender } = await renderProvider();
    expect(screen.getByTestId("total").textContent).toBe("6");

    getUnreadSummary.mockResolvedValue({ conversations: [] });
    authState.isAuthenticated = false;
    await act(async () => rerender(tree()));

    expect(screen.getByTestId("total").textContent).toBe("0");
  });
});
