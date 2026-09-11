// src/components/ChatModal.jsx

import { useEffect, useRef, useState } from "react";
import { X, Plus, Trash2 } from "lucide-react";
import { useChat } from "../hooks/useChat";
import { useAuth } from "../context/AuthContext";
import MessageLimitStatus from "../components/MessageLimitStatus";
import {
  characterCount,
  USER_MESSAGE_LIMIT,
  validUserMessage,
} from "../utils/textLimits";
import { useChatModal } from "../context/ChatModalContext";
import { ChatReminderButton } from "../components/ReminderPanel";
import {
  resetConversation,
  deleteMessage as deleteMessageApi,
  listMessages, // REST fallback for history
} from "../services/conversationService";
import ConfirmDialog from "../components/ConfirmDialog";

/* ---------- Helper components ---------- */
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

function MessageBubble({ sender, text, time, animate = false, onDelete }) {
  const isUser = sender === "user";
  const [showDelete, setShowDelete] = useState(false);

  return (
    <div
      className={`flex ${isUser ? "justify-end" : "justify-start"} ${
        animate ? "animate-[fadeInUp_220ms_ease-out]" : ""
      }`}
      onMouseEnter={() => setShowDelete(true)}
      onMouseLeave={() => setShowDelete(false)}
    >
      <div
        className={`flex max-w-[80%] flex-col ${isUser ? "items-end" : "items-start"}`}
      >
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
              : {
                  borderBottomLeftRadius: "0.5rem",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-soft)",
                  color: "var(--color-text)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }
          }
        >
          {text}
        </div>
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

function formatTimeFromISO(isoString) {
  const d = new Date(isoString);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/* ---------- Main Modal ---------- */

export default function ChatModal() {
  const { isOpen, character, conversationId, closeChat } = useChatModal();
  const { user, isAuthenticated, isLoading: isAuthLoading } = useAuth();

  // Debug: log opening parameters
  useEffect(() => {
    if (isOpen && character) {
      console.log("🚪 ChatModal opened with:", {
        characterId: character?.id || character?.character_id,
        characterName: character?.name,
        conversationId,
        isAuthenticated,
      });
    }
  }, [isOpen, character, conversationId]);

  const {
    messages: chatMessages,
    connecting,
    error,
    sendMessage: sendToAI,
    isSending,
    conversationId: activeConvId,
    clearMessages,
    deleteMessage,
    isCharacterActive,
  } = useChat(
    character?.id || character?.character_id || null,
    isAuthLoading,
    isAuthenticated,
    user,
    conversationId,
  );

  // Debug: monitor message state changes
  useEffect(() => {
    console.log("📨 Messages updated:", {
      count: chatMessages.length,
      activeConvId,
      connecting,
      error,
    });
    if (chatMessages.length > 0) {
      console.log("📋 Last message:", chatMessages[chatMessages.length - 1]);
    }
  }, [chatMessages, activeConvId, connecting, error]);

  const [input, setInput] = useState("");
  const [isWaitingForAI, setIsWaitingForAI] = useState(false);
  const [lastAnimatedMessageId, setLastAnimatedMessageId] = useState(null);
  const [historyFetched, setHistoryFetched] = useState(false); // prevent duplicate REST calls

  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);

  // Confirm dialog state
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

  // Derived messages with formatted timestamps
  const messages = chatMessages.map((msg) => ({
    id: msg.message_id || Math.random(),
    sender: msg.sender_type === "user" ? "user" : "ai",
    text: msg.content,
    time: msg.created_at
      ? formatTimeFromISO(msg.created_at)
      : new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
  }));

  // Stop waiting when AI responds
  useEffect(() => {
    const lastMsg = chatMessages[chatMessages.length - 1];
    if (
      lastMsg &&
      (lastMsg.sender_type === "ai" || lastMsg.sender_type === "system")
    ) {
      setIsWaitingForAI(false);
    }
  }, [chatMessages]);

  // Reset state when the character changes (new conversation)
  useEffect(() => {
    setInput("");
    setIsWaitingForAI(false);
    setLastAnimatedMessageId(null);
    if (textareaRef.current) textareaRef.current.style.height = "24px";
    setHistoryFetched(false); // allow history fetch for new conversation
  }, [character?.id, character?.character_id]);

  // Scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isWaitingForAI]);

  // Auto-resize textarea
  const autoResizeTextarea = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 112)}px`;
  };

  // Send message handler
  const handleSend = async (textToSend = input) => {
    if (!validUserMessage(textToSend)) return;
    const trimmed = textToSend.trim();
    if (!trimmed || isSending || !isCharacterActive) return;
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "24px";
    const ok = await sendToAI(trimmed);
    if (ok) setIsWaitingForAI(true);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Reset conversation confirmation
  const handleResetChat = () => {
    openConfirm(
      "Reset Chat",
      "Are you sure you want to delete all messages in this conversation? This action cannot be undone.",
      async () => {
        if (!activeConvId) return;
        clearMessages();
        await resetConversation(activeConvId);
        closeConfirm();
      },
    );
  };

  // Delete single message confirmation
  const handleDeleteMessage = (msgId) => {
    openConfirm(
      "Delete Message",
      "Permanently delete this message?",
      async () => {
        if (!activeConvId) return;
        deleteMessage(msgId);
        await deleteMessageApi(activeConvId, msgId);
        closeConfirm();
      },
    );
  };

  // Close modal on Escape
  useEffect(() => {
    const handleEsc = (e) => {
      if (e.key === "Escape") closeChat();
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [closeChat]);

  // ---- Fallback: fetch message history via REST if WebSocket didn't load any ----
  useEffect(() => {
    // Only fetch if:
    // - We have an active conversation ID
    // - The connection has finished (no longer connecting)
    // - Auth loading is complete
    // - No messages have arrived yet
    // - We haven't already tried fetching
    if (
      activeConvId &&
      !connecting &&
      !isAuthLoading &&
      chatMessages.length === 0 &&
      !historyFetched
    ) {
      setHistoryFetched(true);
      (async () => {
        try {
          console.log(
            "📥 Fetching message history for conversation:",
            activeConvId,
          );
          const result = await listMessages(activeConvId);
          console.log("📜 Message history response:", result);

          // TODO: Integrate history into the chat state.
          // Currently the WebSocket hook doesn't expose a "loadMessages" method,
          // so we rely on the WebSocket to push history. These logs confirm
          // whether the backend is returning data.
          if (result?.data?.messages?.length) {
            console.log(
              `📜 History contains ${result.data.messages.length} messages – awaiting WebSocket integration.`,
            );
          } else {
            console.log(
              "📜 No historical messages returned (empty conversation or backend issue).",
            );
          }
        } catch (err) {
          console.error("Failed to fetch message history:", err);
        }
      })();
    }
  }, [
    activeConvId,
    connecting,
    isAuthLoading,
    chatMessages.length,
    historyFetched,
  ]);

  if (!isOpen || !character) return null;

  const charName = character.name || "Character";
  const charAvatar = character.avatar || character.avatar_url || null;
  const charRole = character.role || character.short_description || "";
  const isOnline = isCharacterActive;

  const showConnecting = connecting && messages.length === 0;

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-lg h-[90vh] max-h-[800px] flex flex-col rounded-3xl overflow-hidden theme-card border shadow-2xl">
        {/* ---- Fixed Header ---- */}
        <div className="px-5 py-4 border-b border-[var(--color-border)]">
          {/* Row 1: Avatar, Name, Online badge, Action buttons */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3 min-w-0">
              {charAvatar && (
                <img
                  src={charAvatar}
                  alt={charName}
                  className="h-10 w-10 rounded-full object-cover shrink-0"
                  onError={(e) => {
                    e.target.src = "/placeholder.png";
                  }}
                />
              )}
              <div className="flex items-center gap-2 min-w-0">
                <h3 className="text-sm font-bold theme-text truncate">
                  {charName}
                </h3>
                {/* Online badge */}
                <span
                  className={`shrink-0 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium border ${
                    isOnline
                      ? "bg-emerald-500/10 border-emerald-400/30 text-emerald-400"
                      : "bg-gray-500/10 border-gray-400/30 text-gray-400"
                  }`}
                >
                  <span
                    className={`h-2 w-2 rounded-full ${
                      isOnline ? "bg-emerald-400" : "bg-gray-400"
                    }`}
                  />
                  {isOnline ? "Online" : "Offline"}
                </span>
              </div>
            </div>

            {/* Action buttons */}
            <div className="flex items-center gap-1 ml-2">
              <ChatReminderButton
                activeConvId={activeConvId}
                charName={charName}
              />
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

          {/* Row 2: Short description (if present) */}
          {charRole && (
            <p className="mt-1 text-xs theme-text-muted truncate">{charRole}</p>
          )}
        </div>

        {/* ---- Scrollable messages area ---- */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
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
              {messages.map((msg, index) => {
                const prev = messages[index - 1];
                const sameSender = prev && prev.sender === msg.sender;
                return (
                  <div
                    key={msg.id}
                    className={sameSender ? "pt-1" : "pt-3 first:pt-0"}
                  >
                    <MessageBubble
                      sender={msg.sender}
                      text={msg.text}
                      time={msg.time}
                      animate={msg.id === lastAnimatedMessageId}
                      onDelete={() => handleDeleteMessage(msg.id)}
                    />
                  </div>
                );
              })}
              {isWaitingForAI && <TypingBubble characterName={charName} />}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* ---- Inactive character banner ---- */}
        {!isCharacterActive && (
          <div className="mx-4 mb-2 rounded-xl border border-amber-400/30 bg-amber-500/10 p-3 text-center">
            <p className="text-sm text-amber-300">
              This character is no longer available. You can still view and
              delete your past messages.
            </p>
          </div>
        )}

        {/* ---- Fixed footer (input) ---- */}
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
            <button
              type="button"
              className="flex h-11 w-11 items-center justify-center rounded-2xl border text-lg transition hover:opacity-90"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text-muted)",
              }}
            >
              <Plus size={18} />
            </button>

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
                aria-describedby="routed-chat-message-count"
                aria-invalid={characterCount(input) > USER_MESSAGE_LIMIT}
                onChange={(e) => {
                  setInput(e.target.value);
                  autoResizeTextarea();
                }}
                onKeyDown={handleKeyDown}
                placeholder={`Message ${charName}...`}
                disabled={
                  isSending ||
                  isWaitingForAI ||
                  !isCharacterActive ||
                  connecting
                }
                className="min-h-[24px] w-full resize-none bg-transparent text-sm leading-6 theme-text outline-none"
                style={{ maxHeight: "112px" }}
              />
              <MessageLimitStatus
                id="routed-chat-message-count"
                value={input}
              />
            </div>

            <button
              type="submit"
              disabled={
                !validUserMessage(input) ||
                isSending ||
                isWaitingForAI ||
                !isCharacterActive ||
                connecting
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

        {/* ---- Confirm Dialog ---- */}
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
    </div>
  );
}
