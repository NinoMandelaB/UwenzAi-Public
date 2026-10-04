// src/components/ChatModal.jsx
import { useEffect, useRef, useState, useMemo, useCallback, memo } from "react";
import {
  X,
  Trash2,
  AlertTriangle,
  Flag,
  ImageIcon,
  Sparkles as Sparks,
  Globe,
  BrainCircuit,
} from "lucide-react";
import { API } from "../config/constants";
import { resolveMediaUrl } from "../utils/mediaUrl";
import { useChat } from "../hooks/useChat";
import { useConversation } from "../hooks/useConversation";
import { useChatScroll } from "../hooks/useChatScroll";
import { useAuth } from "../context/AuthContext";
import MessageLimitStatus from "./MessageLimitStatus";
import { useLanguage } from "../context/LanguageContext";
import { useSparkPoolBalance } from "../hooks/useSparkPoolBalance";
import { usePricing } from "../hooks/usePricing";
import { localizeImageError } from "../utils/imageErrorMapper";
import { localizeSystemMessage } from "../utils/systemMessage";
import { parseLinks } from "../utils/messageLinks";
import { useChatModal } from "../context/ChatModalContext";
import {
  resetConversation,
  deleteMessage as deleteMessageApi,
  listConversations,
  getConversationSettings,
  updateConversationSettings,
} from "../services/conversationService";
import {
  submitMessageFlag,
  getFlaggedMessageIds,
} from "../services/messageFlagService";
import ConfirmDialog from "./ConfirmDialog";
import CharacterInactiveBanner from "./CharacterInactiveBanner";
import LoadOlderMessages from "./LoadOlderMessages";
import PhotoAttachMenu from "./PhotoAttachMenu";
import { ChatReminderButton } from "./ReminderPanel";
import { resolveFeaturePreferences } from "../utils/featurePreferences";
import MemoryButton from "./MemoryPanel";
import ForegroundPushNotice from "./ForegroundPushNotice";
import {
  characterCount,
  USER_MESSAGE_LIMIT,
  validUserMessage,
} from "../utils/textLimits";

/* ---------- Helper Components ---------- */
const TypingBubble = memo(function TypingBubble({ characterName }) {
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
});

const MessageBubble = memo(function MessageBubble({
  sender,
  text,
  time,
  imageUrl,
  imageAlt = "",
  sharedPhotoAlt = "Shared photo",
  attachments = [],
  animate = false,
  onDelete,
  onFlag,
  isFlagged,
  navigate,
}) {
  const isUser = sender === "user";
  const isSystem = sender === "system";
  const [showActions, setShowActions] = useState(false);
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
      onMouseEnter={() => !isSystem && setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
    >
      <div
        className={`flex max-w-[80%] flex-col ${
          isUser ? "items-end" : "items-start"
        }`}
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
                  }
                : isSystem
                  ? {
                      borderRadius: "0.75rem",
                      border: "1px solid var(--color-accent-soft, #0ea5e9aa)",
                      backgroundColor:
                        "color-mix(in srgb, var(--color-accent) 8%, transparent)",
                      color: "var(--color-text-muted)",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontStyle: "italic",
                      fontSize: "0.8rem",
                    }
                  : {
                      borderBottomLeftRadius: "0.5rem",
                      border: `1px solid ${
                        isFlagged
                          ? "var(--color-warning, #f59e0b)"
                          : "var(--color-border)"
                      }`,
                      backgroundColor: isFlagged
                        ? "color-mix(in srgb, var(--color-warning, #f59e0b) 8%, transparent)"
                        : "var(--color-soft)",
                      color: "var(--color-text)",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    }
            }
          >
            {parseLinks(text, navigate, sender)}
          </div>
        ) : null}
        <span className="mt-1 px-1 text-[11px] theme-text-muted">{time}</span>
      </div>

      {!isSystem && showActions && (
        <div className="flex flex-col ml-2 self-center gap-0.5">
          {!isUser && onFlag && !isFlagged && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onFlag();
              }}
              className="rounded-full p-1 text-gray-400 hover:text-amber-400"
              title="Flag message"
            >
              <Flag size={14} />
            </button>
          )}
          {!isUser && isFlagged && (
            <span
              className="rounded-full p-1 text-amber-500"
              title="Already flagged"
            >
              <Flag size={14} fill="currentColor" />
            </span>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDelete?.();
            }}
            className="rounded-full p-1 text-gray-400 hover:text-red-400"
            title="Delete message"
          >
            <Trash2 size={14} />
          </button>
        </div>
      )}
    </div>
  );
});

/* ---------- Flag Dialog ---------- */
function FlagMessageDialog({ isOpen, onClose, onSubmit }) {
  const [reason, setReason] = useState("");

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!reason.trim()) return;
    onSubmit(reason.trim());
    setReason("");
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[250] flex items-center justify-center bg-black/40 p-4">
      <div className="relative w-full max-w-sm rounded-2xl p-6 shadow-xl theme-card">
        <h3 className="text-lg font-semibold theme-text">Flag Message</h3>
        <p className="mt-1 text-sm theme-text-muted">
          Please explain why you are reporting this message.
        </p>
        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="Enter reason..."
            required
            className="w-full rounded-lg border px-3 py-2 text-sm resize-none"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-soft)",
              color: "var(--color-text)",
            }}
          />
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={() => {
                setReason("");
                onClose();
              }}
              className="rounded-lg px-4 py-2 text-sm font-medium transition-colors"
              style={{
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text-muted)",
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="rounded-lg px-4 py-2 text-sm font-medium text-white transition-opacity"
              style={{ backgroundColor: "var(--color-accent)" }}
            >
              Submit
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ---------- Toast ---------- */
function Toast({ message, visible, onHide, type = "success" }) {
  useEffect(() => {
    if (visible) {
      const timer = setTimeout(onHide, 3000);
      return () => clearTimeout(timer);
    }
  }, [visible, onHide]);

  if (!visible) return null;

  return (
    <div className="fixed bottom-6 left-1/2 z-[300] -translate-x-1/2 animate-[fadeInUp_200ms_ease-out]">
      <div
        className="rounded-full px-4 py-2 text-sm font-medium shadow-lg text-white"
        style={{
          backgroundColor: type === "success" ? "#16a34a" : "#dc2626",
          boxShadow: "0 10px 30px rgba(0,0,0,0.4)",
        }}
      >
        {message}
      </div>
    </div>
  );
}

function formatTimeFromISO(isoString) {
  const d = new Date(isoString);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/* ---------- Character Photo Offer Modal ---------- */
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

/* ---------- Image Prompt Modal ---------- */
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
      className="fixed inset-0 z-[260] flex items-center justify-center p-4"
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

export function ChatEnhancementControls({
  activeConvId,
  canUseConversationEnhancements,
  isTogglingSettings,
  webSearchEnabled,
  reasoningEffort,
  onToggleWebSearch,
  onToggleReasoningEffort,
  showWebSearch = true,
  showDeepThink = true,
}) {
  const controlsDisabled =
    !canUseConversationEnhancements || !activeConvId || isTogglingSettings;
  const guestTitle =
    "Web search and deep thinking are available to logged-in users.";

  if (!showWebSearch && !showDeepThink) {
    return null;
  }

  return (
    <>
      {showWebSearch && (
        <button
          type="button"
          onClick={onToggleWebSearch}
          disabled={controlsDisabled}
          aria-disabled={controlsDisabled}
          data-testid="web-search-toggle"
          className={`rounded-full p-2 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40 ${
            webSearchEnabled
              ? "text-emerald-500"
              : "text-[var(--color-text-muted)]"
          }`}
          title={
            !canUseConversationEnhancements
              ? guestTitle
              : webSearchEnabled
                ? "Web search on for this chat (click to turn off)"
                : "Web search off for this chat (click to turn on)"
          }
          aria-pressed={webSearchEnabled}
        >
          <Globe size={16} />
        </button>
      )}
      {showDeepThink && (
        <button
          type="button"
          onClick={onToggleReasoningEffort}
          disabled={controlsDisabled}
          aria-disabled={controlsDisabled}
          data-testid="reasoning-effort-toggle"
          className={`rounded-full p-2 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40 ${
            reasoningEffort === "deep"
              ? "text-indigo-500"
              : "text-[var(--color-text-muted)]"
          }`}
          title={
            !canUseConversationEnhancements
              ? guestTitle
              : reasoningEffort === "deep"
                ? "Deep thinking mode on for this chat (click for Fast)"
                : "Fast mode for this chat (click for Deep thinking)"
          }
          aria-pressed={reasoningEffort === "deep"}
        >
          <BrainCircuit size={16} />
        </button>
      )}
    </>
  );
}

/* ---------- Main Modal ---------- */
export default function ChatModal() {
  const { t } = useLanguage();
  const { isOpen, character, conversationId, closeChat, navigate } =
    useChatModal();
  const { user, isAuthenticated, isLoading: isAuthLoading } = useAuth();

  // Availability only; conversation search/reasoning retain their own state.
  const featurePreferences = resolveFeaturePreferences(
    isAuthenticated && !isAuthLoading ? user : null,
  );
  const { balance: sparkBalance } = useSparkPoolBalance();

  // ── Resolve conversation ID ──
  const [resolvedConvId, setResolvedConvId] = useState(conversationId);
  const [isLookingUp, setIsLookingUp] = useState(false);

  useEffect(() => {
    if (isOpen && character && !conversationId) {
      const charId = character.id || character.character_id;
      if (!charId) return;

      setIsLookingUp(true);
      (async () => {
        try {
          const data = await listConversations();
          if (data?.status === "success" && data.data.conversations) {
            const match = data.data.conversations.find(
              (conv) => conv.character?.character_id === charId,
            );
            if (match) setResolvedConvId(match.conversation_id);
          }
        } catch (err) {
          console.warn("Failed to look up existing conversation:", err);
        } finally {
          setIsLookingUp(false);
        }
      })();
    } else {
      setResolvedConvId(conversationId);
    }
  }, [isOpen, character, conversationId, isAuthenticated]);

  useEffect(() => {
    if (!resolvedConvId) return;
    (async () => {
      try {
        const data = await getFlaggedMessageIds(resolvedConvId);
        if (data.status === "success") {
          setFlaggedMessageIds(new Set(data.data.flagged_message_ids));
        }
      } catch (err) {
        console.warn("Failed to load flagged messages", err);
      }
    })();
  }, [resolvedConvId, isAuthenticated]);

  const isExistingConversation = Boolean(resolvedConvId) && !isLookingUp;

  // ── Hooks ──
  const newChat = useChat(
    isExistingConversation
      ? null
      : character?.id || character?.character_id || null,
    isAuthLoading,
    isAuthenticated,
    user,
  );

  const existingChat = useConversation(
    isExistingConversation ? resolvedConvId : null,
    isAuthenticated,
    isAuthLoading,
  );

  const {
    messages: chatMessages,
    connecting,
    error,
    sendMessage: sendToAI,
    generateImage,
    photoOffer,
    acceptPhotoOffer,
    declinePhotoOffer,
    isRespondingToOffer,
    isSending,
    clearMessages,
    deleteMessage: deleteMessageFromHook,
    isCharacterActive,
    pendingImage,
    attachImage,
    clearPendingImage,
    hasMoreHistory,
    isLoadingOlder,
    olderHistoryError,
    loadOlderMessages,
  } = isExistingConversation ? existingChat : newChat;

  const activeConvId = isExistingConversation
    ? resolvedConvId
    : newChat.conversationId;

  const displayCharacter = isExistingConversation
    ? existingChat.character || character
    : character;

  // ── Per-chat search & reasoning-effort toggles ──
  // Both toggles are persisted server-side on the Conversation model and take
  // effect starting with the conversation's next message (see
  // Backend/capp/views/conversation_views.py::api_conversation_settings).
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [reasoningEffort, setReasoningEffort] = useState("fast");
  const [isTogglingSettings, setIsTogglingSettings] = useState(false);
  const canUseConversationEnhancements = isAuthenticated && !isAuthLoading;

  useEffect(() => {
    if (!activeConvId) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await getConversationSettings(activeConvId);
        if (!cancelled && data) {
          setWebSearchEnabled(Boolean(data.web_search_enabled));
          setReasoningEffort(data.reasoning_effort || "fast");
        }
      } catch (err) {
        console.warn("Failed to load conversation settings", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeConvId]);

  const handleToggleWebSearch = useCallback(async () => {
    if (!canUseConversationEnhancements || !activeConvId || isTogglingSettings)
      return;
    const next = !webSearchEnabled;
    setWebSearchEnabled(next); // optimistic
    setIsTogglingSettings(true);
    try {
      await updateConversationSettings(activeConvId, {
        webSearchEnabled: next,
      });
    } catch (err) {
      console.error("Failed to update web search toggle:", err);
      setWebSearchEnabled(!next); // revert on failure
    } finally {
      setIsTogglingSettings(false);
    }
  }, [
    activeConvId,
    canUseConversationEnhancements,
    webSearchEnabled,
    isTogglingSettings,
  ]);

  const handleToggleReasoningEffort = useCallback(async () => {
    if (!canUseConversationEnhancements || !activeConvId || isTogglingSettings)
      return;
    const next = reasoningEffort === "deep" ? "fast" : "deep";
    const previous = reasoningEffort;
    setReasoningEffort(next); // optimistic
    setIsTogglingSettings(true);
    try {
      await updateConversationSettings(activeConvId, {
        reasoningEffort: next,
      });
    } catch (err) {
      console.error("Failed to update reasoning effort toggle:", err);
      setReasoningEffort(previous); // revert on failure
    } finally {
      setIsTogglingSettings(false);
    }
  }, [
    activeConvId,
    canUseConversationEnhancements,
    reasoningEffort,
    isTogglingSettings,
  ]);

  const [input, setInput] = useState("");
  const [isImageModalOpen, setIsImageModalOpen] = useState(false);
  const [isGeneratingImage, setIsGeneratingImage] = useState(false);
  const [isWaitingForAI, setIsWaitingForAI] = useState(false);
  const [lastAnimatedMessageId, setLastAnimatedMessageId] = useState(null);

  // In-chat image generation is available only to authenticated users, and only
  // once auth restoration has finished so the button never flashes for guests
  // during the initial load / page refresh.
  const canGenerateImage = isAuthenticated && !isAuthLoading;

  const messagesEndRef = useRef(null);
  const messagesContainerRef = useRef(null);
  const textareaRef = useRef(null);
  const loadOlderRef = useRef(null);
  const olderPendingRef = useRef(false);

  // ── Confirm dialog ──
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmTitle, setConfirmTitle] = useState("");
  const [confirmMessage, setConfirmMessage] = useState("");
  const [confirmAction, setConfirmAction] = useState(() => {});
  const openConfirm = useCallback((title, message, action) => {
    setConfirmTitle(title);
    setConfirmMessage(message);
    setConfirmAction(() => action);
    setConfirmOpen(true);
  }, []);
  const closeConfirm = useCallback(() => setConfirmOpen(false), []);

  // ── Flag dialog state ──
  const [flagMessageId, setFlagMessageId] = useState(null);
  const [isFlagging, setIsFlagging] = useState(false);
  const [toastMessage, setToastMessage] = useState("");
  const [toastVisible, setToastVisible] = useState(false);
  const [toastType, setToastType] = useState("success");
  const [flaggedMessageIds, setFlaggedMessageIds] = useState(new Set());

  const showToast = useCallback((msg, type = "success") => {
    setToastMessage(msg);
    setToastType(type);
    setToastVisible(true);
  }, []);
  const hideToast = useCallback(() => setToastVisible(false), []);

  const handleFlagSubmit = useCallback(
    async (reason) => {
      if (!flagMessageId) return;
      setIsFlagging(true);
      try {
        await submitMessageFlag({
          message_id: flagMessageId,
          reason,
        });
        setFlaggedMessageIds((prev) => new Set(prev).add(flagMessageId));
        showToast("Message flagged successfully", "success");
      } catch (err) {
        console.error("Failed to flag message:", err);
        showToast("Failed to flag message", "error");
      } finally {
        setIsFlagging(false);
        setFlagMessageId(null);
      }
    },
    [flagMessageId, isAuthenticated, showToast],
  );

  // ── Pending photo preview URL: one object URL per picked file, revoked on
  // change/unmount so re-renders never leak blob handles. ──
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

  // ── Memoized messages ──
  const messages = useMemo(
    () =>
      chatMessages.map((msg) => ({
        id: msg.message_id || Math.random().toString(),
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
        // User-sent photos: history rows and live messages both carry
        // `attachments` (live sends get them swapped in after the POST).
        attachments: msg.attachments || [],
        time: msg.created_at
          ? formatTimeFromISO(msg.created_at)
          : new Date().toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            }),
      })),
    [chatMessages, t],
  );

  // ── Stop waiting when AI or system message arrives ──
  useEffect(() => {
    const lastMsg = chatMessages[chatMessages.length - 1];
    if (
      lastMsg &&
      (lastMsg.sender_type === "ai" || lastMsg.sender_type === "system")
    ) {
      setIsWaitingForAI(false);
    }
  }, [chatMessages]);

  // ── Reset on character/conversation change ──
  useEffect(() => {
    setInput("");
    setIsWaitingForAI(false);
    setLastAnimatedMessageId(null);
    if (textareaRef.current) textareaRef.current.style.height = "24px";
  }, [character?.id, character?.character_id, resolvedConvId]);

  // ── Scroll: land at the bottom, follow new messages, hold position on prepend ──
  const { captureAnchor, releaseAnchor } = useChatScroll({
    containerRef: messagesContainerRef,
    bottomRef: messagesEndRef,
    conversationKey: activeConvId,
    messageCount: messages.length,
    lastMessageId: messages[messages.length - 1]?.id,
    isWaitingForAI,
    onNearTop: () => loadOlderRef.current?.(),
  });

  const handleLoadOlder = useCallback(async () => {
    if (olderPendingRef.current || !hasMoreHistory || !loadOlderMessages)
      return;
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

  // ── Auto‑focus textarea when AI response arrives ──
  useEffect(() => {
    if (!isWaitingForAI && !isSending && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isWaitingForAI, isSending]);

  const autoResizeTextarea = useCallback(() => {
    if (textareaRef.current) {
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.style.height = "auto";
        el.style.height = `${Math.min(el.scrollHeight, 112)}px`;
      });
    }
  }, []);

  const handleSend = useCallback(
    async (textToSend = input) => {
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
      if (textareaRef.current) textareaRef.current.style.height = "24px";
      const imageToSend = hasPendingImage ? pendingImage : null;
      if (imageToSend) clearPendingImage();
      const ok = await sendToAI(trimmed, imageToSend);
      if (ok) setIsWaitingForAI(true);
    },
    [
      input,
      isSending,
      isCharacterActive,
      sendToAI,
      pendingImage,
      clearPendingImage,
    ],
  );

  // Enter no longer sends the message: on desktop it now inserts a newline so
  // users can write multi-line messages, and messages are only sent with the
  // Send button (mobile keyboards already insert newlines). No key handling
  // is needed for that — the textarea's default behavior is exactly right.

  // Ensure the image prompt modal can never linger open after logout or a
  // session expiry: close it as soon as image generation is no longer allowed.
  useEffect(() => {
    if (!canGenerateImage && isImageModalOpen) {
      setIsImageModalOpen(false);
    }
  }, [canGenerateImage, isImageModalOpen]);

  const handleGenerateImage = useCallback(
    async (prompt) => {
      if (
        !canGenerateImage ||
        isGeneratingImage ||
        !isCharacterActive ||
        !generateImage
      )
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
    },
    [canGenerateImage, isGeneratingImage, isCharacterActive, generateImage],
  );

  const handleResetChat = useCallback(() => {
    openConfirm(
      "Reset Chat",
      "Are you sure you want to delete all messages in this conversation? This action cannot be undone.",
      async () => {
        try {
          if (activeConvId) {
            clearMessages();
            await resetConversation(activeConvId);
          }
        } catch (err) {
          console.error("Failed to reset chat:", err);
        } finally {
          closeConfirm();
        }
      },
    );
  }, [activeConvId, clearMessages, openConfirm, closeConfirm]);

  const handleDeleteMessage = useCallback(
    (msgId) => {
      openConfirm(
        "Delete Message",
        "Permanently delete this message?",
        async () => {
          try {
            if (activeConvId) {
              deleteMessageFromHook(msgId);
              await deleteMessageApi(activeConvId, msgId);
            }
          } catch (err) {
            console.error("Failed to delete message:", err);
          } finally {
            closeConfirm();
          }
        },
      );
    },
    [activeConvId, deleteMessageFromHook, openConfirm, closeConfirm],
  );

  useEffect(() => {
    const handleEsc = (e) => {
      if (e.key === "Escape") closeChat();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [closeChat]);

  if (!isOpen || !displayCharacter) return null;

  const charName = displayCharacter.name || "Character";
  const charAvatar =
    displayCharacter.avatar_url || displayCharacter.avatar || null;
  const charRole =
    displayCharacter.short_description || displayCharacter.role || "";
  const isOnline = isCharacterActive;
  const showConnecting = connecting && messages.length === 0;
  const hasError = !connecting && error;
  const displayCharacterState =
    displayCharacter?.chat_state || displayCharacter?.review_status || null;
  const showInactiveBanner =
    !isCharacterActive &&
    ["archived", "rejected", "suspended"].includes(displayCharacterState);

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div
        className="w-full max-w-lg h-[90vh] max-h-[800px] flex flex-col rounded-3xl overflow-hidden theme-card border shadow-2xl"
        style={{ willChange: "transform" }}
      >
        {/* ---- Header ---- */}
        <div className="px-5 py-4 border-b border-[var(--color-border)]">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3 min-w-0">
              {charAvatar && (
                <img
                  src={charAvatar}
                  alt={charName}
                  className="h-10 w-10 rounded-full object-cover shrink-0 cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation();
                    const charId =
                      character?.id ||
                      displayCharacter?.id ||
                      displayCharacter?.character_id;
                    if (charId && navigate) {
                      navigate(`/characters/${charId}`);
                    }
                  }}
                  title="View character details"
                  onError={(e) => {
                    e.target.src = "/placeholder.png";
                  }}
                />
              )}
              <div className="flex flex-col min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-bold theme-text truncate">
                    {charName}
                  </h3>
                  <span
                    className={`shrink-0 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium border ${
                      isOnline
                        ? "bg-emerald-500/10 border-emerald-400/30 text-emerald-400"
                        : "bg-gray-500/10 border-gray-400/30 text-gray-400"
                    }`}
                  >
                    <span
                      className={`h-2 w-2 rounded-full ${isOnline ? "bg-emerald-400" : "bg-gray-400"}`}
                    />
                    {isOnline ? "Online" : "Offline"}
                  </span>
                </div>
                {charRole && (
                  <p className="text-xs theme-text-muted truncate">
                    {charRole}
                  </p>
                )}
                {/* Spark balance – global, updates via WebSocket */}
                {sparkBalance !== null && (
                  <div className="flex items-center gap-1 text-[11px] font-medium text-amber-500 mt-0.5">
                    <Sparks size={12} strokeWidth={3} />
                    <span>
                      {sparkBalance.toLocaleString()} {t("spark_pool.sparks")}
                    </span>
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center gap-1 ml-2">
              <ChatEnhancementControls
                activeConvId={activeConvId}
                canUseConversationEnhancements={canUseConversationEnhancements}
                isTogglingSettings={isTogglingSettings}
                webSearchEnabled={webSearchEnabled}
                reasoningEffort={reasoningEffort}
                onToggleWebSearch={handleToggleWebSearch}
                onToggleReasoningEffort={handleToggleReasoningEffort}
                showWebSearch={Boolean(featurePreferences.websearch)}
                showDeepThink={Boolean(featurePreferences.deepthink)}
              />
              <ChatReminderButton
                activeConvId={activeConvId}
                charName={charName}
              />
              {/* Memory editor (brain) — logged-in users who enabled the
                  feature in Settings → Advanced; guests have no persisted
                  memory, so the icon stays hidden for them. */}
              {isAuthenticated &&
                !isAuthLoading &&
                featurePreferences.memory_editor && (
                  <MemoryButton
                    characterId={
                      character?.id ||
                      displayCharacter?.id ||
                      displayCharacter?.character_id
                    }
                    charName={charName}
                  />
                )}
              <button
                onClick={handleResetChat}
                className="rounded-full p-2 hover:bg-gray-200 dark:hover:bg-gray-800 text-[var(--color-text-muted)]"
                title="Reset conversation"
              >
                <Trash2 size={16} />
              </button>
              <button
                onClick={closeChat}
                className="rounded-full p-2 hover:bg-gray-200 dark:hover:bg-gray-800 text-[var(--color-text-muted)]"
              >
                <X size={18} />
              </button>
            </div>
          </div>
        </div>

        {/* Error banner */}
        {hasError && (
          <div className="mx-4 mt-2 rounded-xl border border-amber-400/30 bg-amber-500/10 p-2 flex items-center gap-2 text-sm text-amber-300">
            <AlertTriangle size={16} />
            {error}
          </div>
        )}

        {/* Lookup loader */}
        {isLookingUp && (
          <div className="flex items-center justify-center py-4 text-sm theme-text-muted">
            Loading conversation...
          </div>
        )}

        {/* Messages */}
        <div
          ref={messagesContainerRef}
          className="flex-1 overflow-y-auto px-4 py-4 space-y-4"
        >
          {isAuthLoading ? (
            <p className="text-center text-sm theme-text-muted">
              Starting conversation…
            </p>
          ) : showConnecting ? (
            <div className="flex justify-center py-6">
              <span className="text-sm theme-text-muted">
                Connecting to {charName}…
              </span>
            </div>
          ) : messages.length === 0 && !isWaitingForAI ? (
            <div className="flex justify-center py-6">
              <span className="text-sm theme-text-muted">
                Say hello to {charName}!
              </span>
            </div>
          ) : (
            <div className="space-y-2">
              <LoadOlderMessages
                hasMore={hasMoreHistory}
                isLoading={isLoadingOlder}
                error={olderHistoryError}
                onLoadOlder={handleLoadOlder}
              />
              {messages.map((msg, index) => {
                const prev = messages[index - 1];
                const sameSender = prev && prev.sender === msg.sender;
                const isFlagged = flaggedMessageIds.has(msg.id);
                return (
                  <div
                    key={msg.id}
                    className={sameSender ? "pt-1" : "pt-3 first:pt-0"}
                  >
                    <MessageBubble
                      sender={msg.sender}
                      text={msg.text}
                      time={msg.time}
                      imageUrl={msg.imageUrl}
                      imageAlt={t("chat.generatedImageAlt")}
                      sharedPhotoAlt={t("chat.sharedPhotoAlt")}
                      attachments={msg.attachments}
                      animate={msg.id === lastAnimatedMessageId}
                      onDelete={() => handleDeleteMessage(msg.id)}
                      onFlag={
                        msg.sender === "ai" && !isFlagged
                          ? () => setFlagMessageId(msg.id)
                          : undefined
                      }
                      isFlagged={isFlagged}
                      navigate={navigate}
                    />
                  </div>
                );
              })}
              {isWaitingForAI && <TypingBubble characterName={charName} />}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* Inactive banner */}
        {showInactiveBanner && <CharacterInactiveBanner />}

        {/* Pending photo preview: shown while a picked photo waits for send. */}
        {pendingImage instanceof File && (
          <div className="flex items-center gap-2 border-t border-[var(--color-border)] bg-[var(--color-card)] px-4 py-2">
            <img
              src={pendingImagePreviewUrl}
              alt={t("chat.attachPhoto")}
              className="h-12 w-12 rounded-lg object-cover"
              style={{ border: "1px solid var(--color-border)" }}
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

        {/* Footer */}
        <div
          className="p-3 border-t border-[var(--color-border)] bg-[var(--color-card)]"
          style={{
            paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 12px)",
          }}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
            className="flex items-end gap-2"
          >
            {/* Photo sends: only shown when the deployment explicitly
                enables them (auth/me photo_uploads_enabled, refreshed on
                login). A missing value hides the button — never a dead
                feature (F6). */}
            {isAuthenticated && user?.photo_uploads_enabled === true && (
              <PhotoAttachMenu
                disabled={
                  !isCharacterActive ||
                  isSending ||
                  isWaitingForAI ||
                  connecting ||
                  hasError
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
                  hasError
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
                rows={1}
                value={input}
                aria-describedby="user-message-count"
                aria-invalid={characterCount(input) > USER_MESSAGE_LIMIT}
                onChange={(e) => {
                  setInput(e.target.value);
                  autoResizeTextarea();
                }}
                placeholder={`Message ${charName}...`}
                disabled={
                  isSending ||
                  isWaitingForAI ||
                  !isCharacterActive ||
                  connecting ||
                  hasError
                }
                className="min-h-[24px] w-full resize-none bg-transparent text-sm leading-6 theme-text outline-none"
                style={{ maxHeight: "112px" }}
              />
              <MessageLimitStatus id="user-message-count" value={input} />
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
                hasError
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
        </div>

        <ForegroundPushNotice conversationId={activeConvId} />
        {/* Confirm Dialog */}
        <ConfirmDialog
          isOpen={confirmOpen}
          title={confirmTitle}
          message={confirmMessage}
          confirmText="Delete"
          isLoading={false}
          onConfirm={confirmAction}
          onClose={closeConfirm}
        />

        {/* In-chat image generation modal (authenticated users only).
            Mounted conditionally, not just hidden: it calls usePricing(), and
            /pricing/ is authenticated-only, so mounting it for a guest fires a
            401 that the api client reads as an expired session. */}
        {canGenerateImage && (
          <ImagePromptModal
            open={isImageModalOpen}
            characterName={charName}
            onClose={() => setIsImageModalOpen(false)}
            onSubmit={handleGenerateImage}
            submitting={isGeneratingImage}
          />
        )}

        {/* Character-initiated photo offer popup (authenticated users only).
            Same conditional-mount reasoning as the image modal: accepting is a
            paid, authenticated-only action. The offer arrives over the chat
            WebSocket as a photo_offer message; without this mount, the hook
            captures it but nothing renders it. */}
        {canGenerateImage && (
          <PhotoOfferModal
            offer={photoOffer}
            characterName={charName}
            onAccept={acceptPhotoOffer}
            onDecline={declinePhotoOffer}
            responding={isRespondingToOffer}
          />
        )}

        {/* Flag Message Dialog */}
        <FlagMessageDialog
          isOpen={!!flagMessageId}
          onClose={() => setFlagMessageId(null)}
          onSubmit={handleFlagSubmit}
        />

        {/* Toast notification */}
        <Toast
          message={toastMessage}
          visible={toastVisible}
          onHide={hideToast}
          type={toastType}
        />
      </div>
    </div>
  );
}
