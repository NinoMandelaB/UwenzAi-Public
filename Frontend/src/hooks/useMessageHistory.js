// src/hooks/useMessageHistory.js

import { useCallback, useEffect, useRef, useState } from "react";
import { listMessages } from "../services/conversationService";
import {
  mergeOlderMessages,
  normalizeHistoryMessage,
  readPagination,
} from "../utils/messageHistory";

/**
 * Owns a conversation's transcript: the newest page on mount and older pages on
 * demand, walked with the cursor the API returns.
 *
 * The newest page is fetched once per conversation. Auth restoration or a
 * WebSocket reconnect must not refetch it, or the older pages the user has
 * already pulled in would disappear and the view would snap back to the bottom.
 * A generation counter plus an AbortController make responses for a conversation
 * the user has left, or for a modal that has closed, no-ops.
 */
export function useMessageHistory(
  conversationId,
  { isAuthLoading = false } = {},
) {
  const [messages, setMessages] = useState([]);
  const [character, setCharacter] = useState(null);
  const [loadingHistory, setLoadingHistory] = useState(Boolean(conversationId));
  const [historyError, setHistoryError] = useState(null);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [isLoadingOlder, setIsLoadingOlder] = useState(false);
  const [olderHistoryError, setOlderHistoryError] = useState(null);

  const generationRef = useRef(0);
  const loadedConversationRef = useRef(null);
  const nextBeforeRef = useRef(null);
  const olderInFlightRef = useRef(false);

  useEffect(() => {
    if (!conversationId || isAuthLoading) return;
    if (loadedConversationRef.current === conversationId) return;

    const generation = ++generationRef.current;
    const controller = new AbortController();

    setMessages([]);
    setHasMoreHistory(false);
    setOlderHistoryError(null);
    setHistoryError(null);
    setLoadingHistory(true);
    nextBeforeRef.current = null;

    (async () => {
      try {
        const data = await listMessages(conversationId, {
          signal: controller.signal,
        });
        if (generation !== generationRef.current) return;

        const page = (data?.data?.messages || []).map(normalizeHistoryMessage);
        const { hasMore, nextBefore } = readPagination(data?.data);

        setCharacter(data?.data?.character || null);
        setMessages(page);
        setHasMoreHistory(hasMore);
        nextBeforeRef.current = nextBefore;
        loadedConversationRef.current = conversationId;
      } catch (err) {
        if (generation !== generationRef.current) return;
        if (err?.name === "AbortError") return;
        setHistoryError(err);
      } finally {
        if (generation === generationRef.current) setLoadingHistory(false);
      }
    })();

    return () => controller.abort();
  }, [conversationId, isAuthLoading]);

  /**
   * Fetch the next older page. Resolves to true when messages were prepended,
   * so the caller can decide whether to restore the scroll anchor.
   */
  const loadOlderMessages = useCallback(async () => {
    const cursor = nextBeforeRef.current;
    if (!conversationId || !cursor || olderInFlightRef.current) return false;

    const generation = generationRef.current;
    olderInFlightRef.current = true;
    setIsLoadingOlder(true);
    setOlderHistoryError(null);

    try {
      const data = await listMessages(conversationId, { before: cursor });
      if (generation !== generationRef.current) return false;

      const page = (data?.data?.messages || []).map(normalizeHistoryMessage);
      const { hasMore, nextBefore } = readPagination(data?.data);

      setMessages((previous) => mergeOlderMessages(previous, page));
      setHasMoreHistory(hasMore);
      nextBeforeRef.current = nextBefore;
      return page.length > 0;
    } catch (err) {
      if (generation === generationRef.current) setOlderHistoryError(err);
      return false;
    } finally {
      olderInFlightRef.current = false;
      if (generation === generationRef.current) setIsLoadingOlder(false);
    }
  }, [conversationId]);

  /** Drop the transcript after a reset, and allow it to be fetched again. */
  const resetHistory = useCallback(() => {
    generationRef.current += 1;
    loadedConversationRef.current = null;
    nextBeforeRef.current = null;
    olderInFlightRef.current = false;
    setMessages([]);
    setHasMoreHistory(false);
    setOlderHistoryError(null);
  }, []);

  return {
    messages,
    setMessages,
    character,
    loadingHistory,
    historyError,
    hasMoreHistory,
    isLoadingOlder,
    olderHistoryError,
    loadOlderMessages,
    resetHistory,
  };
}
