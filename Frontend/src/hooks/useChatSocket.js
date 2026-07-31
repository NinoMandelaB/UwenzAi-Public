// src/hooks/useChatSocket.js

import { useEffect, useRef } from "react";
import { WS, WS_CHANNELS } from "../config/constants";
import { getAccessToken } from "../services/apiClient";
import { issueGuestWsTicket } from "../utils/session";

/**
 * Open the chat WebSocket for a conversation.
 *
 * Authenticated callers pass their short-lived access token in the URL as
 * before. Guests cannot: their credential is an HttpOnly cookie that no script
 * can read, and a WebSocket URL is recorded by every proxy it crosses. They
 * exchange the cookie for a ticket that is bound to this conversation, expires
 * in seconds and is spent by the first connection to present it.
 *
 * Because the ticket is fetched asynchronously, the effect may resolve after the
 * component has moved on — hence the cancellation guard around the socket.
 */
export function useChatSocket(conversationId, isAuthenticated, handlers = {}) {
  const socketRef = useRef(null);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!conversationId) return undefined;

    const token = isAuthenticated ? getAccessToken() : null;
    if (isAuthenticated && !token) return undefined;

    let cancelled = false;
    let socket = null;

    const open = async () => {
      let url;
      if (isAuthenticated) {
        url = WS.CHAT(conversationId, token, false);
      } else {
        const ticket = await issueGuestWsTicket(
          WS_CHANNELS.CHAT,
          conversationId,
        );
        if (!ticket) {
          handlersRef.current.onError?.();
          return;
        }
        url = WS.CHAT(conversationId, ticket, true);
      }

      if (cancelled) return;

      socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => handlersRef.current.onOpen?.();
      socket.onmessage = (event) => {
        try {
          handlersRef.current.onMessage?.(JSON.parse(event.data));
        } catch (e) {
          console.error("useChatSocket message parse error:", e);
        }
      };
      socket.onerror = () => handlersRef.current.onError?.();
    };

    handlersRef.current.onConnecting?.();
    open();

    return () => {
      cancelled = true;
      if (socket) socket.close();
      socketRef.current = null;
    };
  }, [conversationId, isAuthenticated]);

  return socketRef;
}
