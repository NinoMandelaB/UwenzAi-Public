import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useChat } from "./useChat";
import { useChatSocket } from "./useChatSocket";
import {
  sendMessage,
  startConversation,
} from "../services/conversationService";
vi.mock("./useChatSocket", () => ({ useChatSocket: vi.fn() }));
vi.mock("./useMarkConversationRead", () => ({
  useMarkConversationRead: () => () => {},
}));
vi.mock("./useMessageHistory", () => ({
  useMessageHistory: () => {
    const [messages, setMessages] = useState([]);
    return { messages, setMessages, loadingHistory: false };
  },
}));
const t = (key) => key;
vi.mock("../context/LanguageContext", () => ({ useLanguage: () => ({ t }) }));
vi.mock("../services/conversationService", () => ({
  startConversation: vi.fn(),
  sendMessage: vi.fn(),
}));
beforeEach(() => {
  vi.clearAllMocks();
  startConversation.mockResolvedValue({
    status: "success",
    data: { conversation_id: "c-1" },
  });
  sendMessage.mockResolvedValue({});
});
afterEach(cleanup);
it.each(["x".repeat(801), ` ${"x".repeat(800)}`, null, {}, ""])(
  "rejects invalid USER input before optimism or a network send",
  async (text) => {
    const { result } = renderHook(() => useChat("char"));
    await waitFor(() => expect(result.current.conversationId).toBe("c-1"));
    await act(async () =>
      expect(await result.current.sendMessage(text)).toBe(false),
    );
    expect(sendMessage).not.toHaveBeenCalled();
    expect(result.current.messages).toHaveLength(0);
  },
);
it("accepts exactly 800 Unicode code points and does not cap AI output", async () => {
  const { result } = renderHook(() => useChat("char"));
  await waitFor(() => expect(result.current.conversationId).toBe("c-1"));
  const input = "\u{1F981}".repeat(800);
  await act(async () =>
    expect(await result.current.sendMessage(input)).toBe(true),
  );
  expect(sendMessage).toHaveBeenCalledWith(
    "c-1",
    { content: input },
    expect.any(String),
  );
  const handlers = useChatSocket.mock.calls.at(-1)[2];
  const longReply = "a".repeat(3000);
  act(() =>
    handlers.onMessage({
      message_id: "m-ai",
      sender_type: "ai",
      content: longReply,
      created_at: "2026-09-10T00:00:00Z",
    }),
  );
  expect(result.current.messages.at(-1).content).toBe(longReply);
});
