// src/hooks/useConversation.js

import { useState, useEffect, useCallback, useRef } from "react";
import {
  characterCount,
  USER_MESSAGE_LIMIT,
  validUserMessage,
} from "../utils/textLimits";
import { useChatSocket } from "./useChatSocket";
import { useMessageHistory } from "./useMessageHistory";
import { clearOperationId, reuseOperationId } from "../utils/idempotency";
import { appendLiveMessage } from "../utils/messageHistory";
import { localizeSendError } from "../utils/sendErrorMapper";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import { validatePhotoFile } from "../utils/photoUpload";
import { useMarkConversationRead } from "./useMarkConversationRead";
import {
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

export function useConversation(
  conversationId,
  isAuthenticated = true,
  isAuthLoading = false,
) {
  const { t } = useLanguage();
  const { user } = useAuth();
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

  // ---------- Re-open a pending photo offer after a reload ----------
  // Live offers arrive over the socket (below); a page refresh only sees them
  // in history, so surface the newest still-pending one as the popup again.
  useEffect(() => {
    if (loadingHistory || historyError) return;
    const pending = [...messages]
      .reverse()
      .find((m) => m.type === "photo_offer" && m.offer?.status === "pending");
    if (pending && !photoOffer) {
      setPhotoOffer({
        messageId: pending.message_id,
        prompt: pending.offer.prompt || "",
        cost: pending.offer.cost ?? null,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingHistory, historyError, messages]);

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

  // ---------- Read state ----------
  const markReadOnLiveMessage = useMarkConversationRead(
    conversationId,
    !loadingHistory && !historyError,
  );

  // ---------- WebSocket connection ----------
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

      // Instantly deactivate the conversation when the farewell message arrives
      if (
        msg.sender_type === "system" &&
        msg.content &&
        msg.content.includes("no longer available")
      ) {
        setIsCharacterActive(false);
      }

      // The server counts this message as unread and pushes a badge for it; the
      // user is reading it right now, so hand the watermark straight back.
      markReadOnLiveMessage();

      window.dispatchEvent(new Event("token-balance-update"));
    },
  });

  // ---------- Send message ----------
  const sendUserMessage = useCallback(
    async (content, image = null) => {
      const hasImage = image instanceof File;
      if (!conversationId) return false;
      // With a photo the caption may be empty, but never over-length.
      const captionOk = hasImage
        ? characterCount(content) <= USER_MESSAGE_LIMIT
        : validUserMessage(content);
      if (!captionOk) return false;

      // The action key must cover the image too: retrying the same photo send
      // reuses the same key so the backend can replay it, never double-charge.
      const actionKey = hasImage
        ? `photo:${image.name}:${image.size}:${image.lastModified}:${content}`
        : content;
      const operationId = reuseOperationId(sendOperationRef, actionKey);

      setError(null);
      setIsSending(true);

      const optimisticMsg = {
        sender_type: "user",
        content,
        // Local preview while the request is in flight; replaced by the
        // server-signed URL on success and by history reloads.
        attachments: hasImage
          ? [
              {
                attachment_id: null,
                url: URL.createObjectURL(image),
                content_type: image.type,
              },
            ]
          : [],
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimisticMsg]);

      try {
        const result = await sendMessageApi(
          conversationId,
          hasImage ? { content, image } : { content },
          operationId,
        );
        clearOperationId(sendOperationRef);
        const serverAttachments = result?.data?.attachments;
        if (
          hasImage &&
          Array.isArray(serverAttachments) &&
          serverAttachments.length > 0
        ) {
          URL.revokeObjectURL(optimisticMsg.attachments[0]?.url);
          setMessages((prev) =>
            prev.map((m) =>
              m === optimisticMsg
                ? {
                    ...m,
                    attachments: serverAttachments,
                    message_id: result?.data?.message_id || m.message_id,
                  }
                : m,
            ),
          );
        }
        return true;
      } catch (err) {
        console.error("useConversation sendUserMessage error:", err);
        setMessages((prev) => prev.filter((m) => m !== optimisticMsg));
        if (optimisticMsg.attachments[0]?.url?.startsWith("blob:")) {
          URL.revokeObjectURL(optimisticMsg.attachments[0].url);
        }
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

  // ---------- Character-initiated photo offers ----------
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
      console.error("useConversation acceptPhotoOffer error:", err);
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
      console.error("useConversation declinePhotoOffer error:", err);
    } finally {
      setIsRespondingToOffer(false);
    }
  }, [photoOffer, conversationId, isRespondingToOffer]);

  // ---------- Message management helpers ----------
  const clearMessages = useCallback(() => {
    resetHistory();
  }, [resetHistory]);

  const deleteMessage = useCallback((messageId) => {
    setMessages((prev) => prev.filter((m) => m.message_id !== messageId));
  }, []);

  // ---------- Pending photo attach state ----------
  const [pendingImage, setPendingImage] = useState(null);

  // Guardrails before an upload is even attempted: the byte and pixel
  // limits come from auth/me (user.photo_max_*), so the user gets an
  // immediate warning with the exact maximum instead of a failed send.
  // The backend re-checks everything; this is UX, not enforcement.
  const attachImage = useCallback(
    async (file) => {
      if (!(file instanceof File)) return;
      if (!file.type.startsWith("image/")) return;
      setError(null);
      const check = await validatePhotoFile(file, {
        maxBytes: user?.photo_max_bytes,
        maxMegapixels: user?.photo_max_megapixels,
      });
      if (!check.ok) {
        setError(t(`errors.${check.code}`, { max: check.max }));
        return;
      }
      setPendingImage(file);
    },
    [t, user],
  );
  const clearPendingImage = useCallback(() => setPendingImage(null), []);

  return {
    messages,
    character,
    connecting,
    loadingHistory,
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
    pendingImage,
    attachImage,
    clearPendingImage,
    hasMoreHistory,
    isLoadingOlder,
    olderHistoryError,
    loadOlderMessages,
  };
}
