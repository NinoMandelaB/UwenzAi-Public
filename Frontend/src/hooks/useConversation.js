// src/hooks/useConversation.js

import { useState, useEffect, useCallback, useRef } from "react";
import { useChatSocket } from "./useChatSocket";
import { useMessageHistory } from "./useMessageHistory";
import { clearOperationId, reuseOperationId } from "../utils/idempotency";
import { appendLiveMessage } from "../utils/messageHistory";
import { localizeSendError } from "../utils/sendErrorMapper";
import { useLanguage } from "../context/LanguageContext";
import {
  sendMessage as sendMessageApi,
  generateChatImage as generateChatImageApi,
} from "../services/conversationService";

function isCharacterUnavailableError(error) {
  return (
    error?.code === "character_unavailable" ||
    error?.payload?.data === "character_unavailable" ||
    error?.message?.includes("no longer available")
  );
}

export function useConversation(
  conversationId,
  isAuthenticated = true,
  isAuthLoading = false,
) {
  const { t } = useLanguage();
  const [connecting, setConnecting] = useState(true);
  const [error, setError] = useState(null);
  const [isSending, setIsSending] = useState(false);

  // One idempotency key per paid action, held across retries of that action.
  const sendOperationRef = useRef(null);
  const imageOperationRef = useRef(null);
  const [isCharacterActive, setIsCharacterActive] = useState(true);

  const {
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
  } = useMessageHistory(conversationId, { isAuthLoading });

  // ---------- Derive character availability from the loaded page ----------
  useEffect(() => {
    if (!character) return;
    setIsCharacterActive(
      character.chat_state
        ? !["archived", "rejected", "suspended"].includes(character.chat_state)
        : character.is_active !== false,
    );
  }, [character]);

  useEffect(() => {
    if (!historyError) return;
    setConnecting(false);
    if (historyError.status === 403) {
      setError("You do not have access to this conversation.");
    } else {
      setError(historyError.message || "Failed to load messages");
    }
    if (isCharacterUnavailableError(historyError)) {
      setIsCharacterActive(false);
    }
  }, [historyError]);

  // ---------- WebSocket connection ----------
  useChatSocket(conversationId, isAuthenticated, {
    onConnecting: () => setConnecting(true),
    onOpen: () => setConnecting(false),
    onError: () => setError("Live connection error"),
    onMessage: (msg) => {
      setMessages((prev) => appendLiveMessage(prev, msg));

      // Instantly deactivate the conversation when the farewell message arrives
      if (
        msg.sender_type === "system" &&
        msg.content &&
        msg.content.includes("no longer available")
      ) {
        setIsCharacterActive(false);
      }

      window.dispatchEvent(new Event("token-balance-update"));
    },
  });

  // ---------- Send message ----------
  const sendUserMessage = useCallback(
    async (content) => {
      if (!conversationId || !content.trim()) return false;

      const operationId = reuseOperationId(sendOperationRef, content);

      setError(null);
      setIsSending(true);

      const optimisticMsg = {
        sender_type: "user",
        content,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimisticMsg]);

      try {
        await sendMessageApi(conversationId, { content }, operationId);
        clearOperationId(sendOperationRef);
        return true;
      } catch (err) {
        console.error("useConversation sendUserMessage error:", err);
        setMessages((prev) => prev.filter((m) => m !== optimisticMsg));
        // The key is deliberately kept on failure: this may have been a
        // timeout on a request the server accepted and charged, so retrying
        // the same text must present the same key, not pay twice.
        setError(localizeSendError(t, err));
        return false;
      } finally {
        setIsSending(false);
      }
    },
    [conversationId, t],
  );

  // ---------- Generate in-chat image ----------
  const generateImage = useCallback(
    async (prompt) => {
      // Chat image generation is an authenticated-only feature; guests never
      // reach here from the UI, but guard defensively in case auth expires.
      if (!isAuthenticated) return { ok: false };
      if (!conversationId || !prompt.trim()) return { ok: false };

      const operationId = reuseOperationId(imageOperationRef, prompt);

      setIsSending(true);

      const optimisticMsg = {
        sender_type: "user",
        content: prompt,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimisticMsg]);

      try {
        await generateChatImageApi(conversationId, { prompt }, operationId);
        clearOperationId(imageOperationRef);
        window.dispatchEvent(new Event("token-balance-update"));
        return { ok: true };
      } catch (err) {
        console.error("useConversation generateImage error:", err);
        setMessages((prev) => prev.filter((m) => m !== optimisticMsg));
        setError(err?.message || "Failed to generate image");
        return { ok: false, code: err?.code, message: err?.message };
      } finally {
        setIsSending(false);
      }
    },
    [conversationId, isAuthenticated],
  );

  // ---------- Message management helpers ----------
  const clearMessages = useCallback(() => {
    resetHistory();
  }, [resetHistory]);

  const deleteMessage = useCallback((messageId) => {
    setMessages((prev) => prev.filter((m) => m.message_id !== messageId));
  }, []);

  return {
    messages,
    character,
    connecting,
    loadingHistory,
    error,
    sendMessage: sendUserMessage,
    generateImage,
    isSending,
    clearMessages,
    deleteMessage,
    isCharacterActive,
    hasMoreHistory,
    isLoadingOlder,
    olderHistoryError,
    loadOlderMessages,
  };
}
