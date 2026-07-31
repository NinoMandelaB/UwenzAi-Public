// src/context/ChatModalContext.jsx
import { createContext, useContext, useReducer, useCallback } from "react";

const ChatModalContext = createContext(null);

const initialState = {
  isOpen: false,
  character: null,
  conversationId: null,
};

function reducer(state, action) {
  switch (action.type) {
    case "OPEN":
      return {
        isOpen: true,
        character: action.payload.character,
        conversationId: action.payload.conversationId || null,
      };
    case "CLOSE":
      return { ...initialState };
    default:
      return state;
  }
}

export function ChatModalProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  const openChat = useCallback((character, conversationId = null) => {
    dispatch({ type: "OPEN", payload: { character, conversationId } });
  }, []);

  const closeChat = useCallback(() => {
    dispatch({ type: "CLOSE" });
  }, []);

  // *** RELIABLE NAVIGATION – no router hook needed ***
  const navigate = useCallback((path) => {
    window.location.href = path;
  }, []);

  return (
    <ChatModalContext.Provider
      value={{ ...state, openChat, closeChat, navigate }}
    >
      {children}
    </ChatModalContext.Provider>
  );
}

export function useChatModal() {
  const context = useContext(ChatModalContext);
  if (!context)
    throw new Error("useChatModal must be used within ChatModalProvider");
  return context;
}
