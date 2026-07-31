import { create } from "zustand";

const useChatModalStore = create((set) => ({
  isOpen: false,
  character: null,
  conversationId: null,

  openChat: (character, conversationId = null) =>
    set({ isOpen: true, character, conversationId }),
  closeChat: () =>
    set({ isOpen: false, character: null, conversationId: null }),
}));

export default useChatModalStore;
