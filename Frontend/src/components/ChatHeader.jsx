// src/components/ChatHeader.jsx

import { useState, useRef, useEffect } from "react";
import { ArrowLeft, ChevronDown, MoreVertical, Trash2 } from "lucide-react";
import StatusBadge from "./StatusBadge";
import { ChatReminderButton } from "./ReminderPanel";
import africanWomanAvatar from "../assets/images/avatars/african-woman.png";

export default function ChatHeader({
  character,
  activeConvId,
  onBack,
  onMore,
  onReset, // NEW – called when user taps "Reset Chat"
  showQuickReplies,
  onToggleQuickReplies,
  quickReplies,
  onSendQuickReply,
}) {
  const { name, role, avatar } = character;
  const [showMore, setShowMore] = useState(false);
  const moreRef = useRef(null);

  // Close the menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (moreRef.current && !moreRef.current.contains(e.target)) {
        setShowMore(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Decide what to do when the MoreVertical button is clicked.
  // If onReset is provided we open our own dropdown; otherwise we call onMore (if provided).
  const handleMoreClick = () => {
    if (onReset) {
      setShowMore((prev) => !prev);
    } else if (onMore) {
      onMore();
    }
  };

  return (
    <header
      className="sticky top-0 z-20 shrink-0 px-4 py-3"
      style={{ backgroundColor: "var(--color-card)" }}
    >
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          className="flex h-10 w-10 items-center justify-center rounded-2xl border transition hover:opacity-90"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-soft)",
            color: "var(--color-text)",
          }}
        >
          <ArrowLeft size={18} />
        </button>

        <img
          src={avatar || africanWomanAvatar}
          alt={name}
          className="h-11 w-11 rounded-2xl object-cover"
        />

        <div className="min-w-0 flex-1">
          <h1 className="truncate text-sm font-semibold theme-text">{name}</h1>
          <div className="mt-0.5 flex flex-wrap items-center gap-1">
            <StatusBadge status="online" />
            <p className="hidden truncate text-xs theme-text-muted sm:block">
              {role}
            </p>
          </div>
        </div>

        <ChatReminderButton activeConvId={activeConvId} charName={name} />
        <button
          type="button"
          onClick={onToggleQuickReplies}
          className="flex h-10 w-10 items-center justify-center rounded-2xl border transition hover:opacity-90"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-soft)",
            color: "var(--color-text)",
          }}
        >
          <ChevronDown
            size={18}
            className={`transition ${showQuickReplies ? "rotate-180" : ""}`}
          />
        </button>

        {/* MoreVertical – now opens a Reset Chat dropdown when onReset is given */}
        <div className="relative" ref={moreRef}>
          <button
            type="button"
            onClick={handleMoreClick}
            className="flex h-10 w-10 items-center justify-center rounded-2xl border transition hover:opacity-90"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-soft)",
              color: "var(--color-text)",
            }}
          >
            <MoreVertical size={18} />
          </button>

          {showMore && onReset && (
            <div
              className="absolute right-0 top-12 min-w-[160px] rounded-2xl border p-2 shadow-lg theme-card"
              style={{ borderColor: "var(--color-border)" }}
              onClick={() => setShowMore(false)}
            >
              <button
                onClick={onReset}
                className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-red-400 hover:bg-red-500/10"
              >
                <Trash2 size={16} />
                Reset Chat
              </button>
            </div>
          )}
        </div>
      </div>

      {/* QUICK REPLIES */}
      <div
        className={`overflow-hidden transition-all duration-200 ${
          showQuickReplies ? "max-h-32 pt-3 opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="flex flex-wrap gap-2">
          {quickReplies.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => onSendQuickReply(item)}
              className="rounded-full border px-3 py-2 text-xs font-medium transition hover:opacity-90"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text-muted)",
              }}
            >
              {item}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
}
