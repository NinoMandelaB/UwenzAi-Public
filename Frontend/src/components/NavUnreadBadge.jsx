// src/components/NavUnreadBadge.jsx
import { useUnread } from "../context/UnreadContext";

/**
 * The count on the Chats nav icon, in both the mobile bar and the desktop
 * sidebar. Renders nothing at zero so the icon keeps its normal footprint.
 */
export default function NavUnreadBadge() {
  const { totalUnread } = useUnread();
  if (totalUnread <= 0) return null;

  return (
    <span
      aria-label={`${totalUnread} unread messages`}
      className="absolute -right-2 -top-1.5 inline-flex h-4 min-w-[16px] items-center justify-center rounded-full px-1 text-[10px] font-semibold leading-none"
      style={{ backgroundColor: "var(--color-accent)", color: "#082f49" }}
    >
      {totalUnread > 99 ? "99+" : totalUnread}
    </span>
  );
}
