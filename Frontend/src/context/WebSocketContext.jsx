// src/context/WebSocketContext.jsx
import React, {
  createContext,
  useEffect,
  useRef,
  useCallback,
  useMemo,
} from "react";

export const WS_STATUS = {
  CONNECTING: "connecting",
  CONNECTED: "connected",
  DISCONNECTED: "disconnected",
  RECONNECTING: "reconnecting",
  ERROR: "error",
};

export const WebSocketContext = createContext(null);

// Credentials that must never reach a log line. Guest tickets are single-use and
// short-lived, but a leaked one is still a leaked one until it expires.
const SECRET_PARAMS = ["token", "ticket"];

export const WebSocketProvider = ({ children }) => {
  const connectionsRef = useRef({});

  const maskUrl = useCallback((url) => {
    if (!url) return url;
    try {
      const urlObj = new URL(url);
      const params = new URLSearchParams(urlObj.search);
      let masked = false;
      SECRET_PARAMS.forEach((name) => {
        if (params.has(name)) {
          params.set(name, `[${name}]`);
          masked = true;
        }
      });
      if (!masked) return url;
      urlObj.search = params.toString();
      return urlObj.toString();
    } catch {
      return url; // fallback
    }
  }, []);

  const logStatus = useCallback(
    (status, url, extra = "") => {
      const timestamp = new Date().toISOString().slice(11, 19);
      const statuses = [
        WS_STATUS.CONNECTING,
        WS_STATUS.CONNECTED,
        WS_STATUS.DISCONNECTED,
        WS_STATUS.RECONNECTING,
        WS_STATUS.ERROR,
      ];
      if (statuses.includes(status) || status === "already_open") {
        console.log(
          `[${timestamp}] [WS] ${maskUrl(url)} => ${status} ${extra}`,
        );
      }
    },
    [maskUrl],
  );

  // scheduleReconnect runs from timers that outlive a render, so it reaches the
  // logger through a ref rather than closing over a specific render's copy.
  const logStatusRef = useRef(logStatus);
  logStatusRef.current = logStatus;

  const scheduleReconnect = useCallback((key, connectFn) => {
    const conn = connectionsRef.current[key];
    if (!conn) return;
    const retryCount = conn.retryCount || 0;
    const delay = Math.min(30000, 1000 * Math.pow(2, retryCount));
    logStatusRef.current(
      WS_STATUS.RECONNECTING,
      key,
      `attempt ${retryCount + 1}, delay ${delay}ms`,
    );
    if (conn.reconnectTimer) clearTimeout(conn.reconnectTimer);
    conn.reconnectTimer = setTimeout(() => {
      const current = connectionsRef.current[key];
      if (!current) return;
      current.retryCount = retryCount + 1;
      connectFn(key);
    }, delay);
  }, []);

  /**
   * Open a socket for `key`, resolving a fresh URL for every attempt.
   *
   * The per-attempt resolution is what makes guest tickets workable: a ticket is
   * spent by the connection that used it, so a reconnect has to mint a new one
   * instead of replaying the URL it was given at registration.
   */
  const connect = useCallback(
    async (key) => {
      const conn = connectionsRef.current[key];
      if (!conn) return;

      if (conn.ws && conn.ws.readyState === WebSocket.OPEN) {
        logStatus("already_open", key);
        return;
      }
      if (conn.connecting) return;

      conn.connecting = true;
      const generation = conn.generation;

      let url;
      try {
        url = conn.resolveUrl ? await conn.resolveUrl() : conn.url;
      } catch {
        url = null;
      }

      // Unsubscribed, or re-registered under new credentials, while we waited.
      const live = connectionsRef.current[key];
      if (!live || live !== conn || live.generation !== generation) {
        return;
      }
      conn.connecting = false;

      if (!url) {
        conn.status = WS_STATUS.DISCONNECTED;
        scheduleReconnect(key, connect);
        return;
      }

      if (conn.ws) {
        // A superseded socket must not drive the reconnect loop for the new one.
        conn.ws.onclose = null;
        conn.ws.close();
      }

      logStatus(WS_STATUS.CONNECTING, url);
      const ws = new WebSocket(url);
      conn.ws = ws;
      conn.status = WS_STATUS.CONNECTING;

      ws.onopen = () => {
        logStatus(WS_STATUS.CONNECTED, url);
        const current = connectionsRef.current[key];
        if (!current || current.ws !== ws) return;
        current.retryCount = 0;
        current.status = WS_STATUS.CONNECTED;
        if (current.pingInterval) clearInterval(current.pingInterval);
        current.pingInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, 30000);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "pong") return;
          const current = connectionsRef.current[key];
          if (current) {
            current.listeners.forEach((listener) => listener(data));
          }
        } catch (err) {
          console.warn(`WebSocket message parse error (${maskUrl(url)}):`, err);
        }
      };

      ws.onerror = () => {
        logStatus(WS_STATUS.ERROR, url);
        const current = connectionsRef.current[key];
        if (current && current.ws === ws) current.status = WS_STATUS.ERROR;
      };

      ws.onclose = (event) => {
        logStatus(
          WS_STATUS.DISCONNECTED,
          url,
          `code=${event.code} reason=${event.reason}`,
        );
        const current = connectionsRef.current[key];
        if (!current || current.ws !== ws) return;
        if (current.pingInterval) clearInterval(current.pingInterval);
        current.status = WS_STATUS.DISCONNECTED;
        scheduleReconnect(key, connect);
      };
    },
    [logStatus, maskUrl, scheduleReconnect],
  );

  /**
   * Subscribe to a socket.
   *
   * `target` is either a URL string, or `{ key, resolveUrl }` where `resolveUrl`
   * is an async function producing a single-use URL. The explicit `key` keeps the
   * connection identity stable even though each attempt's URL differs.
   */
  const register = useCallback(
    (target, options = {}) => {
      const isFactory = target && typeof target === "object";
      const key = isFactory ? target.key : target;

      if (!key) {
        return {
          send: () => {},
          unsubscribe: () => {},
          isConnected: false,
          status: WS_STATUS.DISCONNECTED,
        };
      }

      let conn = connectionsRef.current[key];
      if (!conn) {
        conn = {
          ws: null,
          listeners: new Set(),
          retryCount: 0,
          status: WS_STATUS.DISCONNECTED,
          pingInterval: null,
          reconnectTimer: null,
          connecting: false,
          generation: 0,
          url: isFactory ? null : target,
          resolveUrl: isFactory ? target.resolveUrl : null,
        };
        connectionsRef.current[key] = conn;
      } else if (isFactory) {
        conn.resolveUrl = target.resolveUrl;
      }

      const listener = options.onMessage || (() => {});
      conn.listeners.add(listener);

      if (!conn.ws || conn.ws.readyState !== WebSocket.OPEN) {
        connect(key);
      }

      return {
        send: (data) => {
          const ws = connectionsRef.current[key]?.ws;
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(data));
          } else {
            console.warn(`WebSocket not open (${key}), cannot send:`, data);
          }
        },
        unsubscribe: () => {
          const current = connectionsRef.current[key];
          if (!current) return;
          current.listeners.delete(listener);
          if (current.listeners.size === 0) {
            current.generation += 1;
            if (current.ws) {
              current.ws.onclose = null;
              current.ws.close();
            }
            if (current.pingInterval) clearInterval(current.pingInterval);
            if (current.reconnectTimer) clearTimeout(current.reconnectTimer);
            delete connectionsRef.current[key];
          }
        },
        isConnected: conn.ws?.readyState === WebSocket.OPEN,
        status: conn.status,
      };
    },
    [connect],
  );

  useEffect(() => {
    return () => {
      Object.keys(connectionsRef.current).forEach((key) => {
        const conn = connectionsRef.current[key];
        if (conn.ws) {
          conn.ws.onclose = null;
          conn.ws.close();
        }
        if (conn.pingInterval) clearInterval(conn.pingInterval);
        if (conn.reconnectTimer) clearTimeout(conn.reconnectTimer);
      });
      connectionsRef.current = {};
    };
  }, []);

  const contextValue = useMemo(() => ({ register }), [register]);

  return (
    <WebSocketContext.Provider value={contextValue}>
      {children}
    </WebSocketContext.Provider>
  );
};
