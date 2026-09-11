import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const script = readFileSync("public/push-sw.js", "utf8");
const payload = {
  title: "Zuri",
  body: "You have a new message.",
  url: "/conversations/c-1",
  unread_count: 2,
};
let handlers, worker;
class Channel {
  constructor() {
    this.port1 = { close: vi.fn(), onmessage: null };
    this.port2 = {
      close: vi.fn(),
      postMessage: (data) => this.port1.onmessage?.({ data }),
    };
  }
}
const client = (ack = false) => ({
  url: "https://app.example/settings",
  visibilityState: "visible",
  postMessage: vi.fn((_message, ports) => {
    if (ack) ports[0].postMessage({ type: "UWENZAI_PUSH_HANDLED" });
  }),
});
const dispatchPush = (data = payload) => {
  let waiting = Promise.resolve();
  handlers.push({
    data: { json: () => data },
    waitUntil: (promise) => {
      waiting = promise;
    },
  });
  return waiting;
};
beforeEach(() => {
  vi.useFakeTimers();
  handlers = {};
  worker = {
    addEventListener: (name, handler) => {
      handlers[name] = handler;
    },
    location: { origin: "https://app.example" },
    navigator: { setAppBadge: vi.fn().mockResolvedValue(undefined) },
    clients: { matchAll: vi.fn().mockResolvedValue([]), openWindow: vi.fn() },
    registration: { showNotification: vi.fn().mockResolvedValue(undefined) },
  };
  runInNewContext(script, {
    self: worker,
    MessageChannel: Channel,
    URL,
    setTimeout,
    clearTimeout,
  });
});
afterEach(() => vi.useRealTimers());

describe("actual production push worker", () => {
  it("falls back to a system alert for visible Settings without an ACK", async () => {
    const settings = client();
    worker.clients.matchAll.mockResolvedValue([settings]);
    const done = dispatchPush();
    await vi.advanceTimersByTimeAsync(999);
    expect(worker.registration.showNotification).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await done;
    expect(settings.postMessage).toHaveBeenCalled();
    expect(worker.registration.showNotification).toHaveBeenCalledWith(
      "Zuri",
      expect.objectContaining({
        body: payload.body,
        data: { url: payload.url },
      }),
    );
    expect(settings.postMessage.mock.calls[0][0]).toMatchObject({ payload });
  });
  it("suppresses only the acknowledged foreground receipt", async () => {
    worker.clients.matchAll.mockResolvedValue([client(true)]);
    await dispatchPush();
    expect(worker.registration.showNotification).not.toHaveBeenCalled();
    expect(worker.navigator.setAppBadge).toHaveBeenCalledWith(2);
  });
  it("ignores unrelated ACK values and hidden/cross-origin clients", async () => {
    const wrong = client();
    wrong.postMessage.mockImplementation((_message, ports) =>
      ports[0].postMessage({ type: "unrelated" }),
    );
    const hidden = { ...client(true), visibilityState: "hidden" };
    const foreign = { ...client(true), url: "https://other.example/" };
    worker.clients.matchAll.mockResolvedValue([wrong, hidden, foreign]);
    const done = dispatchPush();
    await vi.advanceTimersByTimeAsync(1000);
    await done;
    expect(worker.registration.showNotification).toHaveBeenCalledOnce();
    expect(hidden.postMessage).not.toHaveBeenCalled();
    expect(foreign.postMessage).not.toHaveBeenCalled();
  });
  it("does not let a hung badge API suppress the alert", async () => {
    worker.navigator.setAppBadge.mockReturnValue(new Promise(() => {}));
    await dispatchPush();
    expect(worker.registration.showNotification).toHaveBeenCalledOnce();
  });
  it.each([null, [], "bad"])(
    "handles malformed payload %j without throwing",
    async (data) => {
      await dispatchPush(data);
      expect(worker.registration.showNotification).not.toHaveBeenCalled();
    },
  );
  it("keeps notification navigation on this origin", async () => {
    await dispatchPush({ ...payload, url: "https://evil.example/path" });
    expect(worker.registration.showNotification.mock.calls[0][1].data.url).toBe(
      "/",
    );
    let done;
    handlers.notificationclick({
      notification: { close: vi.fn(), data: { url: "javascript:alert(1)" } },
      waitUntil: (p) => {
        done = p;
      },
    });
    await done;
    expect(worker.clients.openWindow).toHaveBeenCalledWith("/");
  });
});
