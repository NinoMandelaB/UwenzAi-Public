// src/pages/Chats.jsx

import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, Pin, CheckCheck, Check, Plus } from "lucide-react";
import africanWomanAvatar from "../assets/images/avatars/african-woman.png";
import { listConversations } from "../services/conversationService";
import { useAuth } from "../context/AuthContext";
import { useChatModal } from "../context/ChatModalContext";
import { useLanguage } from "../context/LanguageContext"; // <-- added import
import { useUnread } from "../context/UnreadContext";

// ==================== Helper Components ====================

function ChatStatusDot({ online, accent }) {
  return (
    <span
      className="absolute bottom-0 right-0 h-3.5 w-3.5 rounded-full border-2"
      style={{
        backgroundColor: online ? accent : "var(--color-text-muted)",
        borderColor: "var(--color-card)",
      }}
    />
  );
}

function DeliveryIcon({ delivered, read }) {
  if (read) {
    return <CheckCheck size={14} style={{ color: "var(--color-accent)" }} />;
  }
  if (delivered) {
    return (
      <CheckCheck size={14} style={{ color: "var(--color-text-muted)" }} />
    );
  }
  return <Check size={14} style={{ color: "var(--color-text-muted)" }} />;
}

function ChatRow({ chat, onClick, t }) {
  // <-- added t prop
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full rounded-3xl border p-3 text-left transition hover:opacity-95 active:scale-[0.99]"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-card)",
      }}
    >
      <div className="flex items-start gap-3">
        <div className="relative shrink-0">
          <img
            src={chat.avatar}
            alt={chat.name}
            className="h-14 w-14 rounded-2xl object-cover"
            onError={(e) => {
              e.target.src = africanWomanAvatar;
            }}
          />
          <ChatStatusDot online={chat.isOnline} accent={chat.accent} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h3 className="truncate text-sm font-semibold theme-text">
                  {chat.name}
                </h3>
                {chat.isPinned && (
                  <Pin size={13} style={{ color: "var(--color-text-muted)" }} />
                )}
              </div>
              <p className="truncate text-[11px] theme-text-muted">
                {chat.role}
              </p>
            </div>
            <p
              className="text-[11px] font-medium shrink-0"
              style={{
                color:
                  chat.unreadCount > 0
                    ? "var(--color-accent)"
                    : "var(--color-text-muted)",
              }}
            >
              {chat.time}
            </p>
          </div>

          <div className="mt-2 flex items-center gap-2">
            <div className="min-w-0 flex-1">
              {chat.isTyping ? (
                <p
                  className="truncate text-sm italic"
                  style={{ color: "var(--color-accent)" }}
                >
                  {t("common.typing")}
                </p>
              ) : (
                <div className="flex items-center gap-1.5">
                  {chat.sentByUser && (
                    <DeliveryIcon delivered={chat.delivered} read={chat.read} />
                  )}
                  <p className="truncate text-sm theme-text-muted">
                    {chat.lastMessage}
                  </p>
                </div>
              )}
            </div>

            {chat.unreadCount > 0 && (
              <span
                className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-full px-1.5 text-[11px] font-semibold"
                style={{
                  backgroundColor: "var(--color-accent)",
                  color: "#082f49",
                }}
              >
                {chat.unreadCount > 99 ? "99+" : chat.unreadCount}
              </span>
            )}
          </div>
        </div>
      </div>
    </button>
  );
}

// ==================== Main Chats Page ====================

export default function Chats() {
  const navigate = useNavigate();
  const { t } = useLanguage(); // <-- added
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();
  const { openChat } = useChatModal();
  const { counts, seed: seedUnread } = useUnread();
  const [query, setQuery] = useState("");
  const [conversations, setConversations] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  // Wait for auth to finish initializing before requesting conversations: the
  // in-memory access token is restored asynchronously on load, and the list
  // endpoint scopes results to the authenticated user. Firing before the token
  // is ready would send an anonymous request and render an empty/guest list for
  // a logged-in user. Because AuthProvider only flips isLoading→false AFTER it
  // has set the session, opening this gate guarantees isAuthenticated is final.
  // Re-run when auth state flips (login/logout) so the list refreshes, and use
  // the `cancelled` flag so a superseded in-flight response can never overwrite
  // the current auth state's data.
  useEffect(() => {
    if (isAuthLoading) return;

    let cancelled = false;

    const fetchConversations = async () => {
      try {
        setIsLoading(true);
        setError(null);

        const data = await listConversations();
        if (!cancelled && data?.status === "success") {
          const mapped = data.data.conversations.map((conv) => {
            const lastMsg = conv.last_message || {};
            const character = conv.character || {};
            const sentByUser =
              lastMsg.sender_type === "user" || lastMsg.sender_type === "USER";

            const msgDate = conv.last_message_at
              ? new Date(conv.last_message_at)
              : null;
            const timeStr = msgDate
              ? msgDate.toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })
              : "";

            return {
              id: conv.conversation_id,
              conversationId: conv.conversation_id,
              name: character.name || "Character",
              role: character.short_description || "",
              lastMessage: lastMsg.preview || lastMsg.content || "",
              time: timeStr,
              unreadCount: conv.unread_count || 0,
              isPinned: false,
              isOnline: true,
              isTyping: false,
              sentByUser,
              delivered: sentByUser,
              read: sentByUser,
              accent: "var(--color-accent)",
              avatar: character.avatar_url || africanWomanAvatar,
              characterId: character.character_id || character.id,
              short_description: character.short_description,
              avatar_url: character.avatar_url,
            };
          });

          setConversations(mapped);
          seedUnread(
            mapped.map((chat) => [chat.conversationId, chat.unreadCount]),
          );
        } else {
          if (!cancelled)
            setError(data?.data || "Could not load conversations");
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    fetchConversations();
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, isAuthLoading, seedUnread]);

  const filteredChats = useMemo(() => {
    const normalized = query.trim().toLowerCase();

    // The context holds the live number: it starts as what this list fetched and
    // is then moved by websocket pushes and by opening a chat, neither of which
    // refetches the list.
    const withUnread = conversations.map((chat) => ({
      ...chat,
      unreadCount: counts[chat.conversationId] || 0,
    }));

    const sorted = withUnread.sort((a, b) => {
      if (a.isPinned && !b.isPinned) return -1;
      if (!a.isPinned && b.isPinned) return 1;
      return 0;
    });

    if (!normalized) return sorted;

    return sorted.filter(
      (chat) =>
        chat.name.toLowerCase().includes(normalized) ||
        chat.role.toLowerCase().includes(normalized) ||
        chat.lastMessage.toLowerCase().includes(normalized),
    );
  }, [conversations, counts, query]);

  const handleChatClick = (chat) => {
    openChat(
      {
        id: chat.characterId,
        name: chat.name,
        avatar: chat.avatar_url || chat.avatar,
        role: chat.short_description || chat.role,
      },
      chat.conversationId,
    );
  };

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <header className="mb-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold theme-text">
              {t("common.chats")}
            </h1>
            <p className="text-sm theme-text-muted">
              {t("common.pickUpWhereLeftOff")}
            </p>
          </div>
          <button
            onClick={() => navigate("/characters")}
            className="flex h-11 w-11 items-center justify-center rounded-2xl border"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-card)",
              color: "var(--color-text)",
            }}
          >
            <Plus size={18} />
          </button>
        </div>

        <div className="mt-4">
          <div
            className="flex items-center gap-3 rounded-2xl border px-4 py-3"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-input)",
            }}
          >
            <Search size={18} style={{ color: "var(--color-text-muted)" }} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("common.searchChats")}
              className="w-full bg-transparent text-sm outline-none"
              style={{ color: "var(--color-text)" }}
            />
          </div>
        </div>
      </header>

      {/* List */}
      <section className="flex-1 space-y-3">
        {isLoading ? (
          <div className="text-center theme-text-muted">
            {t("common.loadingChats")}
          </div>
        ) : error ? (
          <div className="text-center text-red-500">{error}</div>
        ) : filteredChats.length > 0 ? (
          filteredChats.map((chat) => (
            <ChatRow
              key={chat.id}
              chat={chat}
              onClick={() => handleChatClick(chat)}
              t={t}
            />
          ))
        ) : (
          <div className="text-center theme-text-muted">
            {t("common.noChatsFound")}
          </div>
        )}
      </section>
    </div>
  );
}
