// src/hooks/useChat.js

import { useState, useEffect, useCallback, useRef } from "react";
import { useChatSocket } from "./useChatSocket";
import { useMessageHistory } from "./useMessageHistory";
import { useMarkConversationRead } from "./useMarkConversationRead";
import { clearOperationId, reuseOperationId } from "../utils/idempotency";
import { appendLiveMessage } from "../utils/messageHistory";
import { localizeSendError } from "../utils/sendErrorMapper";
import { useLanguage } from "../context/LanguageContext";
import {
  startConversation,
  sendMessage as sendMessageApi,
  generateChatImage as generateChatImageApi,
  acceptPhotoOffer as acceptPhotoOfferApi,
  declinePhotoOffer as declinePhotoOfferApi,
} from "../services/conversationService";
function isCharacterUnavailableError(error) {
  return (
    error?.code === "character_unavailable" ||
    error?.payload?.data === "character_unavailable" ||
    error?.message?.includes("no longer available")
  );
}

export function useChat(
  characterId,
  isAuthLoading = false,
  isAuthenticated = true,
  user = null,
) {
  const { t } = useLanguage();
  const [conversationId, setConversationId] = useState(null);
  const [connecting, setConnecting] = useState(true);
  const [error, setError] = useState(null);
  const [isSending, setIsSending] = useState(false);

  // One idempotency key per paid action, held across retries of that action.
  const sendOperationRef = useRef(null);
  const imageOperationRef = useRef(null);
  const [isCharacterActive, setIsCharacterActive] = useState(true);

  // A pending character-initiated photo offer, surfaced as a popup. Arrives
  // over the chat WebSocket as a message with type "photo_offer".
  const [photoOffer, setPhotoOffer] = useState(null);
  const [isRespondingToOffer, setIsRespondingToOffer] = useState(false);

  const {
    messages,
    setMessages,
    loadingHistory,
    historyError,
    hasMoreHistory,
    isLoadingOlder,
    olderHistoryError,
    loadOlderMessages,
    resetHistory,
  } = useMessageHistory(conversationId, { isAuthLoading });

  useEffect(() => {
    if (!historyError) return;
    setConnecting(false);
    setError(historyError.message || "Failed to load messages");
  }, [historyError]);

  useEffect(() => {
    if (!characterId || isAuthLoading) return;
    let cancelled = false;

    const initConv = async () => {
      if (!cancelled) {
        setIsCharacterActive(true);
        setError(null);
      }

      try {
        const data = await startConversation({ character_id: characterId });
        if (!cancelled && data?.status === "success") {
          // Messages are not cleared here: the transcript for this id is owned
          // by useMessageHistory, which loads the newest page for it.
          setConversationId(data.data.conversation_id);
          setIsCharacterActive(true);
        } else {
          if (!cancelled) {
            setConnecting(false);
          }
          if (data?.data === "character_unavailable") {
            setIsCharacterActive(false);
          } else {
            setError(
              data?.data || data?.message || "Could not start conversation",
            );
          }
        }
      } catch (err) {
        if (!cancelled) {
          setConnecting(false);
          setError(err.message);
          if (isCharacterUnavailableError(err)) {
            setIsCharacterActive(false);
          }
        }
      }
    };

    initConv();
    return () => {
      cancelled = true;
    };
  }, [characterId, isAuthLoading, isAuthenticated, user?.user_id]);

  const markReadOnLiveMessage = useMarkConversationRead(
    conversationId,
    !loadingHistory && !historyError,
  );

  useChatSocket(conversationId, isAuthenticated, {
    onConnecting: () => setConnecting(true),
    onOpen: () => setConnecting(false),
    onError: () => setError("Live connection error"),
    onMessage: (msg) => {
      // Photo offers are not transcript bubbles: they open the accept/decline
      // popup instead of being appended to the message list.
      if (msg.type === "photo_offer" && msg.offer?.status === "pending") {
        setPhotoOffer({
          messageId: msg.message_id,
          prompt: msg.offer.prompt || "",
          cost: msg.offer.cost ?? null,
        });
        markReadOnLiveMessage();
        return;
      }

      setMessages((prev) => appendLiveMessage(prev, msg));

      // Instantly deactivate when farewell message arrives
      if (
        msg.sender_type === "system" &&
        msg.content &&
        msg.content.includes("no longer available")
      ) {
        setIsCharacterActive(false);
      }

      markReadOnLiveMessage();

      window.dispatchEvent(new Event("token-balance-update"));
    },
  });

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
        console.error("useChat sendUserMessage error:", err);
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
        console.error("useChat generateImage error:", err);
        setMessages((prev) => prev.filter((m) => m !== optimisticMsg));
        setError(err?.message || "Failed to generate image");
        return { ok: false, code: err?.code, message: err?.message };
      } finally {
        setIsSending(false);
      }
    },
    [conversationId, isAuthenticated],
  );

  const acceptPhotoOffer = useCallback(async () => {
    if (
      !photoOffer ||
      !conversationId ||
      !isAuthenticated ||
      isRespondingToOffer
    )
      return { ok: false };

    setIsRespondingToOffer(true);
    setError(null);
    try {
      await acceptPhotoOfferApi(conversationId, photoOffer.messageId);
      setPhotoOffer(null);
      window.dispatchEvent(new Event("token-balance-update"));
      return { ok: true };
    } catch (err) {
      console.error("useChat acceptPhotoOffer error:", err);
      // Keep the popup open on failure so the user can retry or decline; only
      // an already-resolved offer is dropped.
      const code = err?.code || err?.payload?.data;
      if (typeof code === "string" && code.startsWith("photo_offer_already_")) {
        setPhotoOffer(null);
      }
      setError(err?.message || "Failed to accept photo");
      return { ok: false, code, message: err?.message };
    } finally {
      setIsRespondingToOffer(false);
    }
  }, [photoOffer, conversationId, isAuthenticated, isRespondingToOffer]);

  const declinePhotoOffer = useCallback(async () => {
    if (!photoOffer || !conversationId || isRespondingToOffer) return;

    setIsRespondingToOffer(true);
    const offerId = photoOffer.messageId;
    setPhotoOffer(null);
    try {
      await declinePhotoOfferApi(conversationId, offerId);
    } catch (err) {
      // Declining is free and idempotent server-side; a failed call only means
      // the transcript card stays "pending", which is harmless.
      console.error("useChat declinePhotoOffer error:", err);
    } finally {
      setIsRespondingToOffer(false);
    }
  }, [photoOffer, conversationId, isRespondingToOffer]);

  const clearMessages = useCallback(() => resetHistory(), [resetHistory]);
  const deleteMessage = useCallback((messageId) => {
    setMessages((prev) => prev.filter((m) => m.message_id !== messageId));
  }, []);

  return {
    conversationId,
    messages,
    connecting,
    error,
    sendMessage: sendUserMessage,
    generateImage,
    photoOffer,
    acceptPhotoOffer,
    declinePhotoOffer,
    isRespondingToOffer,
    isSending,
    clearMessages,
    deleteMessage,
    isCharacterActive,
    loadingHistory,
    hasMoreHistory,
    isLoadingOlder,
    olderHistoryError,
    loadOlderMessages,
  };
}
