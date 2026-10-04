// src/pages/ConversationChat.jsx

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { Trash2, ImageIcon, X } from "lucide-react";
import africanWomanAvatar from "../assets/images/avatars/african-woman.png";
import { useConversation } from "../hooks/useConversation";
import { useChatScroll } from "../hooks/useChatScroll";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import MessageLimitStatus from "../components/MessageLimitStatus";
import {
  characterCount,
  USER_MESSAGE_LIMIT,
  validUserMessage,
} from "../utils/textLimits";
import { API } from "../config/constants";
import { resolveMediaUrl } from "../utils/mediaUrl";
import { localizeImageError } from "../utils/imageErrorMapper";
import { localizeSystemMessage } from "../utils/systemMessage";
import { parseLinks } from "../utils/messageLinks";
import { usePricing } from "../hooks/usePricing";
import ChatHeader from "../components/ChatHeader";
import {
  resetConversation,
  deleteMessage as deleteMessageApi,
} from "../services/conversationService";
import ConfirmDialog from "../components/ConfirmDialog";
import PhotoAttachMenu from "../components/PhotoAttachMenu";
import LoadOlderMessages from "../components/LoadOlderMessages";

function getCurrentTime() {
  return new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatTimeFromISO(isoString) {
  const d = new Date(isoString);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function TypingBubble({ characterName }) {
  return (
    <div className="flex justify-start animate-[fadeIn_180ms_ease-out]">
      <div
        className="rounded-3xl px-4 py-3 text-sm shadow-sm"
        style={{
          border: "1px solid var(--color-border)",
          backgroundColor: "var(--color-soft)",
          color: "var(--color-text-muted)",
          borderBottomLeftRadius: "0.5rem",
        }}
      >
        {characterName} is typing...
      </div>
    </div>
  );
}

function MessageBubble({
  sender,
  text,
  time,
  imageUrl,
  imageAlt = "",
  sharedPhotoAlt = "Shared photo",
  attachments = [],
  animate = false,
  onDelete,
  navigate,
}) {
  const isUser = sender === "user";
  const [showDelete, setShowDelete] = useState(false);
  const resolvedImage = resolveMediaUrl(imageUrl);
  const photoAttachments = (attachments || []).filter((a) => a?.url);
  // Photo-only messages (empty caption) still render their image.
  const showTextBubble =
    Boolean(text && text.trim()) ||
    (!resolvedImage && photoAttachments.length === 0);

  return (
    <div
      className={`flex ${isUser ? "justify-end" : "justify-start"} ${
        animate ? "animate-[fadeInUp_220ms_ease-out]" : ""
      }`}
      onMouseEnter={() => setShowDelete(true)}
      onMouseLeave={() => setShowDelete(false)}
    >
      <div
        className={`flex max-w-[82%] flex-col ${isUser ? "items-end" : "items-start"}`}
      >
        {photoAttachments.map((attachment, index) => (
          <a
            key={attachment.attachment_id || index}
            href={resolveMediaUrl(attachment.url)}
            target="_blank"
            rel="noopener noreferrer"
            className="mb-1 overflow-hidden rounded-3xl shadow-sm"
            style={{
              border: "1px solid var(--color-border)",
              borderBottomLeftRadius: isUser ? undefined : "0.5rem",
              borderBottomRightRadius: isUser ? "0.5rem" : undefined,
            }}
          >
            <img
              src={resolveMediaUrl(attachment.url)}
              alt={sharedPhotoAlt}
              loading="lazy"
              className="block max-h-80 w-full object-cover"
            />
          </a>
        ))}
        {resolvedImage ? (
          <a
            href={resolvedImage}
            target="_blank"
            rel="noopener noreferrer"
            className="overflow-hidden rounded-3xl shadow-sm"
            style={{
              border: "1px solid var(--color-border)",
              borderBottomLeftRadius: isUser ? undefined : "0.5rem",
              borderBottomRightRadius: isUser ? "0.5rem" : undefined,
            }}
          >
            <img
              src={resolvedImage}
              alt={imageAlt}
              loading="lazy"
              className="block max-h-80 w-full object-cover"
            />
          </a>
        ) : showTextBubble ? (
          <div
            className="rounded-3xl px-4 py-3 text-sm leading-6 shadow-sm"
            style={
              isUser
                ? {
                    borderBottomRightRadius: "0.5rem",
                    backgroundColor: "var(--color-accent)",
                    color: "#082f49",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    textDecoration: "none",
                  }
                : {
                    borderBottomLeftRadius: "0.5rem",
                    border: "1px solid var(--color-border)",
                    backgroundColor: "var(--color-soft)",
                    color: "var(--color-text)",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    textDecoration: "none",
                  }
            }
          >
            {parseLinks(text, navigate, sender)}
          </div>
        ) : null}
        <span className="mt-1 px-1 text-[11px] theme-text-muted">{time}</span>
      </div>
      {showDelete && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete?.();
          }}
          className="ml-2 self-center rounded-full p-1 text-gray-400 hover:text-red-400"
        >
          <Trash2 size={14} />
        </button>
      )}
    </div>
  );
}

function ImagePromptModal({
  open,
  characterName,
  onClose,
  onSubmit,
  submitting,
}) {
  const { t } = useLanguage();
  const { pricing, isLoading: pricingLoading } = usePricing();
  const [prompt, setPrompt] = useState("");
  const [localError, setLocalError] = useState("");

  const imageCost = pricing?.image_generation_cost;

  useEffect(() => {
    if (open) {
      setPrompt("");
      setLocalError("");
    }
  }, [open]);

  if (!open) return null;

  const handleSubmit = async (event) => {
    event.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed) {
      setLocalError(t("chat.promptRequired"));
      return;
    }
    setLocalError("");
    const failure = await onSubmit(trimmed);
    if (failure) setLocalError(localizeImageError(t, failure));
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.5)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-2xl p-5 shadow-xl"
        style={{
          backgroundColor: "var(--color-card)",
          border: "1px solid var(--color-border)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between">
          <h2 className="text-base font-semibold theme-text">
            {t("chat.imagePromptTitle")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1 text-gray-400 hover:text-gray-200"
            aria-label={t("chat.cancel")}
          >
            <X size={18} />
          </button>
        </div>

        <p className="mb-3 text-xs theme-text-muted">
          {t("chat.imagePromptSubtitle", { name: characterName })}
        </p>

        <form onSubmit={handleSubmit}>
          <textarea
            autoFocus
            rows="3"
            value={prompt}
            onChange={(event) => {
              setPrompt(event.target.value);
              if (localError) setLocalError("");
            }}
            maxLength={600}
            placeholder={t("chat.imagePromptPlaceholder")}
            className="w-full resize-none rounded-xl border px-3 py-2 text-sm leading-6 theme-text outline-none"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-input)",
            }}
          />

          {/* Price is rendered only once /pricing/ has resolved; showing a
              literal fallback would risk quoting a stale cost. */}
          {typeof imageCost === "number" ? (
            <p className="mt-2 text-xs font-medium theme-text">
              {t("chat.imageCost", { cost: imageCost })}
            </p>
          ) : pricingLoading ? (
            <p className="mt-2 text-xs theme-text-muted">
              {t("chat.imageCostLoading")}
            </p>
          ) : null}

          {/* Policy notice: intentionally small, light and semi-transparent */}
          <p
            className="mt-2 text-[11px] italic"
            style={{ color: "var(--color-text-muted)", opacity: 0.6 }}
          >
            {t("chat.imagePolicyNotice")}
          </p>

          {localError && (
            <p className="mt-2 text-xs text-red-400">{localError}</p>
          )}

          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90"
              style={{
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text-muted)",
              }}
            >
              {t("chat.cancel")}
            </button>
            <button
              type="submit"
              disabled={submitting || !prompt.trim()}
              className="rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              style={{
                backgroundColor: "var(--color-accent-soft)",
                color: "var(--color-accent)",
                border: "1px solid var(--color-accent-soft)",
              }}
            >
              {submitting ? t("chat.generatingImage") : t("chat.generate")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function PhotoOfferModal({
  offer,
  characterName,
  onAccept,
  onDecline,
  responding,
}) {
  const { t } = useLanguage();
  const { pricing } = usePricing();

  if (!offer) return null;

  // The offer message carries the discounted price the backend will charge;
  // the /pricing/ value is only a display fallback until it resolves.
  const cost =
    typeof offer.cost === "number"
      ? offer.cost
      : pricing?.character_photo_offer_cost;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.5)" }}
      onClick={onDecline}
    >
      <div
        className="w-full max-w-sm rounded-2xl p-5 shadow-xl"
        style={{
          backgroundColor: "var(--color-card)",
          border: "1px solid var(--color-border)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between">
          <h2 className="text-base font-semibold theme-text">
            {t("chat.photoOfferTitle", { name: characterName })}
          </h2>
          <button
            type="button"
            onClick={onDecline}
            className="rounded-full p-1 text-gray-400 hover:text-gray-200"
            aria-label={t("chat.cancel")}
          >
            <X size={18} />
          </button>
        </div>

        <p className="mb-3 text-sm theme-text">
          {t("chat.photoOfferQuestion")}
        </p>

        {offer.prompt ? (
          <p className="mb-3 text-xs italic theme-text-muted">
            {t("chat.photoOfferDescription", { description: offer.prompt })}
          </p>
        ) : null}

        {typeof cost === "number" ? (
          <p className="text-xs font-medium theme-text">
            {t("chat.photoOfferCost", { cost })}
          </p>
        ) : null}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onDecline}
            disabled={responding}
            className="rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-soft)",
              color: "var(--color-text-muted)",
            }}
          >
            {t("chat.photoOfferDecline")}
          </button>
          <button
            type="button"
            onClick={onAccept}
            disabled={responding}
            className="rounded-xl px-4 py-2 text-sm font-medium transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              backgroundColor: "var(--color-accent-soft)",
              color: "var(--color-accent)",
              border: "1px solid var(--color-accent-soft)",
            }}
          >
            {responding
              ? t("chat.photoOfferAccepting")
              : t("chat.photoOfferAccept")}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ConversationChat() {
  const navigate = useNavigate();
  const { conversationId } = useParams();
  const location = useLocation();
  const { user, isAuthenticated, isLoading: isAuthLoading } = useAuth();
  const { t } = useLanguage();

  // ---------- Hooks must be called unconditionally ----------

  const {
    messages: chatMessages,
    character: hookCharacter,
    connecting,
    loadingHistory,
    error: chatError,
    sendMessage: sendToAI,
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
  } = useConversation(conversationId, isAuthenticated, isAuthLoading);

  // Determine character display
  const activeCharacter = useMemo(() => {
    if (hookCharacter) {
      return {
        name: hookCharacter.name || "Character",
        role: hookCharacter.short_description || "",
        avatar: hookCharacter.avatar_url || africanWomanAvatar,
      };
    }
    if (location.state?.character) {
      return {
        name: location.state.character.name || "Character",
        role: location.state.character.role || "",
        avatar: location.state.character.avatar || africanWomanAvatar,
      };
    }
    return {
      name: "Character",
      role: "",
      avatar: africanWomanAvatar,
    };
  }, [hookCharacter, location.state]);

  const [input, setInput] = useState("");
  const [isImageModalOpen, setIsImageModalOpen] = useState(false);
  const [isGeneratingImage, setIsGeneratingImage] = useState(false);
  const [isWaitingForAI, setIsWaitingForAI] = useState(false);

  // In-chat image generation is available only to authenticated users, and only
  // once auth restoration has finished so the button never flashes for guests
  // during the initial load / page refresh.
  const canGenerateImage = isAuthenticated && !isAuthLoading;
  // animation state removed (not used)
  const [lastAnimatedMessageId] = useState(null);
  const [showQuickReplies, setShowQuickReplies] = useState(false);

  const messagesEndRef = useRef(null);
  const messagesContainerRef = useRef(null);
  const textareaRef = useRef(null);
  const loadOlderRef = useRef(null);
  const olderPendingRef = useRef(false);

  const quickReplies = useMemo(() => {
    return ["I need encouragement", "Let’s just talk", "Make me laugh"];
  }, []);

  // ---------- Confirmation dialog state ----------
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmTitle, setConfirmTitle] = useState("");
  const [confirmMessage, setConfirmMessage] = useState("");
  const [confirmAction, setConfirmAction] = useState(() => {});

  const openConfirm = (title, message, action) => {
    setConfirmTitle(title);
    setConfirmMessage(message);
    setConfirmAction(() => action);
    setConfirmOpen(true);
  };
  const closeConfirm = () => setConfirmOpen(false);

  // Map messages for display
  const messages = useMemo(() => {
    return chatMessages.map((msg) => ({
      id: msg.message_id || Math.random(),
      sender:
        msg.sender_type === "user"
          ? "user"
          : msg.sender_type === "system"
            ? "system"
            : "ai",
      text:
        msg.sender_type === "system"
          ? localizeSystemMessage(t, msg)
          : msg.content,
      imageUrl: msg.image_url || null,
      attachments: msg.attachments || [],
      time: msg.created_at
        ? formatTimeFromISO(msg.created_at)
        : getCurrentTime(),
    }));
  }, [chatMessages, t]);

  // Stop typing indicator when AI or system message arrives
  useEffect(() => {
    const lastMsg = chatMessages[chatMessages.length - 1];
    if (
      lastMsg &&
      (lastMsg.sender_type === "ai" || lastMsg.sender_type === "system")
    ) {
      setIsWaitingForAI(false);
    }
  }, [chatMessages]);

  // Land at the bottom on load, follow new messages, and hold the reading
  // position when an older page is prepended.
  const { captureAnchor, releaseAnchor } = useChatScroll({
    containerRef: messagesContainerRef,
    bottomRef: messagesEndRef,
    conversationKey: conversationId,
    messageCount: messages.length,
    lastMessageId: messages[messages.length - 1]?.id,
    isWaitingForAI,
    onNearTop: () => loadOlderRef.current?.(),
  });

  const handleLoadOlder = useCallback(async () => {
    if (olderPendingRef.current || !hasMoreHistory) return;
    olderPendingRef.current = true;
    captureAnchor();
    try {
      // Nothing came back: drop the anchor so the next layout pass is not
      // pinned to a scroll height that never changed.
      const prepended = await loadOlderMessages();
      if (!prepended) releaseAnchor();
    } finally {
      olderPendingRef.current = false;
    }
  }, [hasMoreHistory, loadOlderMessages, captureAnchor, releaseAnchor]);

  useEffect(() => {
    loadOlderRef.current = handleLoadOlder;
  }, [handleLoadOlder]);

  // Inject animation keyframes
  useEffect(() => {
    const styleId = "chat-animations";
    if (document.getElementById(styleId)) return;
    const style = document.createElement("style");
    style.id = styleId;
    style.innerHTML = `
      @keyframes fadeInUp {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
      }
      @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
      }
    `;
    document.head.appendChild(style);
    return () => {
      const existing = document.getElementById(styleId);
      if (existing) existing.remove();
    };
  }, []);

  // Auto‑resize textarea
  function autoResizeTextarea() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 112)}px`;
  }

  // Pending photo preview URL: one object URL per picked file, revoked on
  // change/unmount so re-renders never leak blob handles.
  const pendingImagePreviewUrl = useMemo(
    () =>
      pendingImage instanceof File ? URL.createObjectURL(pendingImage) : null,
    [pendingImage],
  );
  useEffect(
    () => () => {
      if (pendingImagePreviewUrl) URL.revokeObjectURL(pendingImagePreviewUrl);
    },
    [pendingImagePreviewUrl],
  );

  // Send message handler
  async function handleSend(textToSend = input) {
    const hasPendingImage = pendingImage instanceof File;
    // A photo send is valid with an empty caption, but the caption length
    // limit applies to photo sends exactly as to text sends.
    const captionOk = hasPendingImage
      ? characterCount(textToSend) <= USER_MESSAGE_LIMIT
      : validUserMessage(textToSend);
    if (!captionOk) return;
    if (isSending || !isCharacterActive) return;
    const trimmed = textToSend.trim();
    if (!trimmed && !hasPendingImage) return;

    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "24px";
    }

    const imageToSend = hasPendingImage ? pendingImage : null;
    if (imageToSend) clearPendingImage();

    const ok = await sendToAI(trimmed, imageToSend);

    if (ok) {
      setIsWaitingForAI(true);
    }
  }

  function handleSubmit(event) {
    event.preventDefault();
    handleSend();
  }

  // Ensure the image prompt modal can never linger open after logout or a
  // session expiry: close it as soon as image generation is no longer allowed.
  useEffect(() => {
    if (!canGenerateImage && isImageModalOpen) {
      setIsImageModalOpen(false);
    }
  }, [canGenerateImage, isImageModalOpen]);

  // Generate an in-chat image of the current character
  async function handleGenerateImage(prompt) {
    if (!canGenerateImage || isGeneratingImage || !isCharacterActive)
      return null;
    setIsGeneratingImage(true);
    const result = await generateImage(prompt);
    setIsGeneratingImage(false);
    if (result?.ok) {
      setIsImageModalOpen(false);
      setIsWaitingForAI(true);
      return null;
    }
    // Returned to the modal so it can render the localized reason in place.
    return result;
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  }

  // ---------- Reset Chat handler ----------
  const handleResetChat = () => {
    openConfirm(
      "Reset Chat",
      "Are you sure you want to delete all messages in this conversation? This action cannot be undone.",
      async () => {
        if (!conversationId) return;
        clearMessages();
        await resetConversation(conversationId);
        closeConfirm();
      },
    );
  };

  // ---------- Delete single message handler ----------
  const handleDeleteMessage = (msgId) => {
    openConfirm(
      "Delete Message",
      "Permanently delete this message?",
      async () => {
        if (!conversationId) return;
        deleteMessage(msgId);
        await deleteMessageApi(conversationId, msgId);
        closeConfirm();
      },
    );
  };

  const showLoading = (loadingHistory || connecting) && messages.length === 0;

  // ---------- Render loading screen while auth initialises ----------
  if (isAuthLoading) {
    return (
      <div
        className="flex h-[100dvh] min-h-0 items-center justify-center"
        style={{ backgroundColor: "var(--color-card-soft)" }}
      >
        <p className="text-sm theme-text-muted">Preparing chat…</p>
      </div>
    );
  }

  return (
    <div className="w-full h-full mx-auto max-w-md lg:mx-0 flex flex-col overflow-hidden">
      {/* HEADER */}
      <ChatHeader
        character={activeCharacter}
        activeConvId={conversationId}
        onBack={() => navigate("/chats")}
        showQuickReplies={showQuickReplies}
        onToggleQuickReplies={() => setShowQuickReplies((prev) => !prev)}
        quickReplies={quickReplies}
        onSendQuickReply={(text) => handleSend(text)}
        onReset={handleResetChat}
      />

      {chatError && (
        <div className="mx-4 mt-2 rounded-xl border border-amber-400/30 bg-amber-500/10 p-2 text-sm text-amber-300">
          {chatError}
        </div>
      )}

      {/* MESSAGES SECTION */}
      <section
        ref={messagesContainerRef}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
      >
        {showLoading && (
          <div className="flex justify-center py-6">
            <span className="text-sm theme-text-muted">Loading messages…</span>
          </div>
        )}

        {!showLoading && messages.length === 0 && !isWaitingForAI && (
          <div className="flex justify-center py-6">
            <span className="text-sm theme-text-muted">
              Say hello to {activeCharacter.name}!
            </span>
          </div>
        )}

        <div className="space-y-2">
          {!showLoading && (
            <LoadOlderMessages
              hasMore={hasMoreHistory}
              isLoading={isLoadingOlder}
              error={olderHistoryError}
              onLoadOlder={handleLoadOlder}
              showStart={messages.length > 0}
            />
          )}
          {messages.map((message, index) => {
            const previousMessage = messages[index - 1];
            const isSameSenderAsPrevious =
              previousMessage && previousMessage.sender === message.sender;

            return (
              <div
                key={message.id}
                className={isSameSenderAsPrevious ? "pt-1" : "pt-3 first:pt-0"}
              >
                <MessageBubble
                  sender={message.sender}
                  text={message.text}
                  time={message.time}
                  imageUrl={message.imageUrl}
                  imageAlt={t("chat.generatedImageAlt")}
                  sharedPhotoAlt={t("chat.sharedPhotoAlt")}
                  attachments={message.attachments}
                  animate={false}
                  navigate={navigate}
                  onDelete={() => handleDeleteMessage(message.id)}
                />
              </div>
            );
          })}

          {isWaitingForAI && (
            <TypingBubble characterName={activeCharacter.name} />
          )}

          <div ref={messagesEndRef} />
        </div>
      </section>

      {/* Inactive character banner */}
      {!isCharacterActive && (
        <div className="mx-4 mb-2 rounded-xl border border-amber-400/30 bg-amber-500/10 p-3 text-center">
          <p className="text-sm text-amber-300">
            This character is no longer available. You can still view and delete
            your past messages.
          </p>
        </div>
      )}

      {/* INPUT FOOTER */}
      <footer
        className="sticky bottom-0 z-20 shrink-0 px-3 py-3"
        style={{
          backgroundColor: "var(--color-card)",
          paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 12px)",
        }}
      >
        {/* Pending photo preview while a picked photo waits for send. */}
        {pendingImage instanceof File && (
          <div
            className="mb-2 flex items-center gap-2 rounded-2xl border px-3 py-2"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-soft)",
            }}
          >
            <img
              src={pendingImagePreviewUrl}
              alt={t("chat.attachPhoto")}
              className="h-10 w-10 rounded-lg object-cover"
            />
            <span className="flex-1 truncate text-xs theme-text-muted">
              {pendingImage.name}
            </span>
            <button
              type="button"
              onClick={clearPendingImage}
              className="rounded-full p-1 text-gray-400 hover:text-red-400"
              title={t("chat.removePhoto")}
              aria-label={t("chat.removePhoto")}
            >
              <X size={16} />
            </button>
          </div>
        )}
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          {/* Photo sends: only shown when the deployment explicitly enables
              them (auth/me photo_uploads_enabled, refreshed on login). A
              missing value hides the button — never a dead feature (F6). */}
          {isAuthenticated && user?.photo_uploads_enabled === true && (
            <PhotoAttachMenu
              disabled={
                !isCharacterActive || isSending || isWaitingForAI || connecting
              }
              onFileSelected={attachImage}
            />
          )}
          {canGenerateImage && (
            <button
              type="button"
              onClick={() => setIsImageModalOpen(true)}
              disabled={
                !isCharacterActive ||
                isGeneratingImage ||
                connecting ||
                loadingHistory
              }
              title={t("chat.generateImage")}
              aria-label={t("chat.generateImage")}
              className="flex h-11 w-11 items-center justify-center rounded-2xl border text-lg transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text-muted)",
              }}
            >
              <ImageIcon size={18} />
            </button>
          )}

          <div
            className="flex-1 rounded-2xl border px-3 py-2"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-input)",
            }}
          >
            <textarea
              ref={textareaRef}
              rows="1"
              value={input}
              aria-describedby="conversation-message-count"
              aria-invalid={characterCount(input) > USER_MESSAGE_LIMIT}
              onChange={(event) => {
                setInput(event.target.value);
                autoResizeTextarea();
              }}
              onKeyDown={handleKeyDown}
              placeholder={`Message ${activeCharacter.name}...`}
              disabled={
                isSending ||
                isWaitingForAI ||
                !isCharacterActive ||
                connecting ||
                loadingHistory
              }
              className="min-h-[24px] w-full resize-none bg-transparent text-sm leading-6 theme-text outline-none"
              style={{ maxHeight: "112px" }}
            />
            <MessageLimitStatus id="conversation-message-count" value={input} />
          </div>

          <button
            type="submit"
            disabled={
              (pendingImage instanceof File
                ? characterCount(input) > USER_MESSAGE_LIMIT
                : !validUserMessage(input)) ||
              isSending ||
              isWaitingForAI ||
              !isCharacterActive ||
              connecting ||
              loadingHistory
            }
            className="rounded-2xl px-4 py-3 text-sm font-medium transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              backgroundColor: "var(--color-accent-soft)",
              color: "var(--color-accent)",
              border: "1px solid var(--color-accent-soft)",
            }}
          >
            {isSending ? "Sending…" : "Send"}
          </button>
        </form>
      </footer>

      {/* In-chat image generation modal (authenticated users only).
          Mounted conditionally, not just hidden: it calls usePricing(), and
          /pricing/ is authenticated-only, so mounting it for a guest fires a
          401 that the api client reads as an expired session. */}
      {canGenerateImage && (
        <ImagePromptModal
          open={isImageModalOpen}
          characterName={activeCharacter.name}
          onClose={() => setIsImageModalOpen(false)}
          onSubmit={handleGenerateImage}
          submitting={isGeneratingImage}
        />
      )}

      {/* Character-initiated photo offer popup (authenticated users only).
          Same conditional-mount reasoning as the image modal above: accepting
          is a paid, authenticated-only action. */}
      {canGenerateImage && (
        <PhotoOfferModal
          offer={photoOffer}
          characterName={activeCharacter.name}
          onAccept={acceptPhotoOffer}
          onDecline={declinePhotoOffer}
          responding={isRespondingToOffer}
        />
      )}

      {/* Custom Confirm Dialog */}
      <ConfirmDialog
        isOpen={confirmOpen}
        title={confirmTitle}
        message={confirmMessage}
        confirmText="Delete"
        isLoading={false}
        onConfirm={confirmAction}
        onClose={closeConfirm}
      />
    </div>
  );
}
