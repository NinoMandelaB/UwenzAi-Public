// src/hooks/useChatScroll.js

import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

// How close to an edge counts as "at" it. Generous enough to survive a partly
// visible bubble and the typing indicator appearing.
const NEAR_BOTTOM_PX = 120;
const NEAR_TOP_PX = 120;

/**
 * Scroll management for a chat transcript.
 *
 * Three behaviours have to coexist: the first page lands at the bottom, a new
 * message pulls the view down only for a user who is already there, and a page
 * of older history keeps the message the user was reading exactly where it was.
 * The last one is why the anchor is captured before the request and applied in a
 * layout effect — after paint the browser has already moved the content down.
 */
export function useChatScroll({
  containerRef,
  bottomRef,
  conversationKey,
  messageCount,
  lastMessageId,
  isWaitingForAI,
  onNearTop,
}) {
  const anchorRef = useRef(null);
  const hasLandedRef = useRef(false);
  const nearBottomRef = useRef(true);
  const onNearTopRef = useRef(onNearTop);

  useEffect(() => {
    onNearTopRef.current = onNearTop;
  }, [onNearTop]);

  useEffect(() => {
    hasLandedRef.current = false;
    nearBottomRef.current = true;
    anchorRef.current = null;
  }, [conversationKey]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;

    const handleScroll = () => {
      const distanceFromBottom =
        element.scrollHeight - element.scrollTop - element.clientHeight;
      nearBottomRef.current = distanceFromBottom <= NEAR_BOTTOM_PX;

      if (element.scrollTop <= NEAR_TOP_PX) onNearTopRef.current?.();
    };

    element.addEventListener("scroll", handleScroll, { passive: true });
    return () => element.removeEventListener("scroll", handleScroll);
  }, [containerRef, conversationKey]);

  /** Record the viewport before an older page is prepended. */
  const captureAnchor = useCallback(() => {
    const element = containerRef.current;
    anchorRef.current = element
      ? { scrollHeight: element.scrollHeight, scrollTop: element.scrollTop }
      : null;
  }, [containerRef]);

  /** Forget a captured anchor, e.g. when the request brought nothing back. */
  const releaseAnchor = useCallback(() => {
    anchorRef.current = null;
  }, []);

  useLayoutEffect(() => {
    const element = containerRef.current;
    const anchor = anchorRef.current;

    if (anchor && element) {
      anchorRef.current = null;
      element.scrollTop =
        anchor.scrollTop + (element.scrollHeight - anchor.scrollHeight);
      return;
    }

    if (messageCount === 0) return;

    if (!hasLandedRef.current) {
      hasLandedRef.current = true;
      bottomRef.current?.scrollIntoView({ block: "end" });
      return;
    }

    if (nearBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [containerRef, bottomRef, messageCount, lastMessageId, isWaitingForAI]);

  return { captureAnchor, releaseAnchor };
}
