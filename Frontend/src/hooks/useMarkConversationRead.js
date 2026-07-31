// src/hooks/useMarkConversationRead.js
import { useCallback, useEffect } from "react";
import { useUnread } from "../context/UnreadContext";

/**
 * Clears a conversation's unread badge while the user is looking at it.
 *
 * Having the history on screen is what counts as reading it, so the watermark
 * moves once the history has loaded and again whenever a live message lands.
 * A hidden tab is not reading: messages that arrive while the user is away
 * keep their badge until they come back to it.
 *
 * Returns the handler to call for each live message.
 */
export function useMarkConversationRead(conversationId, isReady) {
  const { markRead } = useUnread();

  useEffect(() => {
    if (!conversationId || !isReady) return;
    if (document.visibilityState !== "visible") return;
    markRead(conversationId);
  }, [conversationId, isReady, markRead]);

  useEffect(() => {
    if (!conversationId) return;
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") markRead(conversationId);
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [conversationId, markRead]);

  return useCallback(() => {
    if (!conversationId) return;
    if (document.visibilityState !== "visible") return;
    markRead(conversationId);
  }, [conversationId, markRead]);
}
