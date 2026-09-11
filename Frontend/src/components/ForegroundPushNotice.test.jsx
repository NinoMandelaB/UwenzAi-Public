import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ForegroundPushNotice from "./ForegroundPushNotice";

let scope = {
  user: { user_id: "A" },
  sessionGeneration: 1,
  isAuthenticated: true,
};
vi.mock("../context/AuthContext", () => ({ useAuth: () => scope }));
const t = (key) => key;
vi.mock("../context/LanguageContext", () => ({ useLanguage: () => ({ t }) }));
let worker;
beforeEach(() => {
  scope = {
    user: { user_id: "A" },
    sessionGeneration: 1,
    isAuthenticated: true,
  };
  worker = new EventTarget();
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: worker,
  });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
});
afterEach(cleanup);
const receipt = (url = "/conversations/c-1") => {
  const port = { postMessage: vi.fn(), close: vi.fn() };
  act(() =>
    worker.dispatchEvent(
      new MessageEvent("message", {
        data: {
          type: "UWENZAI_PUSH_RECEIPT",
          payload: { title: "Zuri", body: "New message.", url },
        },
        ports: [port],
      }),
    ),
  );
  return port;
};
it("ACKs only after the actual in-app notice renders", () => {
  render(<ForegroundPushNotice conversationId="c-1" />);
  const port = receipt();
  expect(screen.getByRole("status").textContent).toContain("New message.");
  expect(port.postMessage).toHaveBeenCalledWith({
    type: "UWENZAI_PUSH_HANDLED",
  });
  fireEvent.click(screen.getByText("chat.dismissNotification"));
  expect(screen.queryByRole("status")).toBe(null);
});
it("does not ACK another conversation, a hidden tab, or a logged-out session", () => {
  const view = render(<ForegroundPushNotice conversationId="c-1" />);
  expect(receipt("/conversations/c-2").postMessage).not.toHaveBeenCalled();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  expect(receipt().postMessage).not.toHaveBeenCalled();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  scope = { ...scope, isAuthenticated: false, sessionGeneration: 2 };
  view.rerender(<ForegroundPushNotice conversationId="c-1" />);
  expect(receipt().postMessage).not.toHaveBeenCalled();
});
