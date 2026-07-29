// src/hooks/useWebSocket.js
import { useContext } from "react";
import { WebSocketContext } from "../context/WebSocketContext";

export const useWebSocket = (url, options = {}) => {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error("useWebSocket must be used within a WebSocketProvider");
  }
  return context.register(url, options);
};
