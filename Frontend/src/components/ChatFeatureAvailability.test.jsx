import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ChatModal from "./ChatModal";
import LegacyChat from "../pages/Chat";
import ConversationChat from "../pages/ConversationChat";
import ReminderButton from "./ReminderPanel";
import {
  getConversationSettings,
  updateConversationSettings,
} from "../services/conversationService";
import { listReminders } from "../services/reminderService";

const character = { id: "c1", character_id: "c1", name: "Test companion" };
const chatState = {
  messages: [],
  character,
  conversationId: "conv1",
  connecting: false,
  isCharacterActive: true,
  sendMessage: vi.fn(),
  loadOlderMessages: vi.fn(),
};
let auth;
vi.mock("../context/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("../context/LanguageContext", () => ({
  useLanguage: () => ({ t: (key) => key, language: "en" }),
}));
vi.mock("../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: true }),
}));
vi.mock("../context/ChatModalContext", () => ({
  useChatModal: () => ({
    isOpen: true,
    character,
    conversationId: "conv1",
    closeChat: vi.fn(),
  }),
}));
vi.mock("../hooks/useChat", () => ({ useChat: () => chatState }));
vi.mock("../hooks/useConversation", () => ({
  useConversation: () => chatState,
}));
vi.mock("../hooks/useChatScroll", () => ({
  useChatScroll: () => ({ captureAnchor: vi.fn(), releaseAnchor: vi.fn() }),
}));
vi.mock("../hooks/useSparkPoolBalance", () => ({
  useSparkPoolBalance: () => ({ balance: null }),
}));
vi.mock("../hooks/usePricing", () => ({ usePricing: () => ({}) }));
vi.mock("./ForegroundPushNotice", () => ({ default: () => null }));
vi.mock("../services/messageFlagService", () => ({
  getFlaggedMessageIds: vi.fn().mockResolvedValue({
    status: "success",
    data: { flagged_message_ids: [] },
  }),
  submitMessageFlag: vi.fn(),
}));
vi.mock("../services/conversationService", () => ({
  getConversationSettings: vi
    .fn()
    .mockResolvedValue({ web_search_enabled: false, reasoning_effort: "fast" }),
  updateConversationSettings: vi.fn(),
  resetConversation: vi.fn(),
  deleteMessage: vi.fn(),
  listConversations: vi.fn().mockResolvedValue([]),
  listMessages: vi.fn().mockResolvedValue([]),
}));
vi.mock("../services/reminderService", () => ({
  listReminders: vi.fn().mockResolvedValue([]),
  createReminder: vi.fn(),
  updateReminder: vi.fn(),
  deleteReminder: vi.fn(),
}));
const tree = (Surface) => (
  <MemoryRouter initialEntries={["/conversations/conv1"]}>
    <Routes>
      <Route path="/conversations/:conversationId" element={<Surface />} />
    </Routes>
  </MemoryRouter>
);

beforeEach(() => {
  vi.clearAllMocks();
  auth = {
    user: { user_id: "A" },
    isAuthenticated: true,
    isLoading: false,
    sessionGeneration: 1,
  };
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

describe.each([
  ["global chat modal", ChatModal],
  ["conversation page", ConversationChat],
  ["legacy chat route", LegacyChat],
])("%s reminder availability", (_name, Surface) => {
  it("shows absent defaults, hides on saved false and restores on true", async () => {
    const view = render(tree(Surface));
    expect(screen.getByRole("button", { name: "Reminders" })).toBeTruthy();
    auth = {
      ...auth,
      user: { ...auth.user, feature_preferences: { reminders: false } },
    };
    view.rerender(tree(Surface));
    expect(screen.queryByRole("button", { name: "Reminders" })).toBeNull();
    auth = {
      ...auth,
      user: { ...auth.user, feature_preferences: { reminders: true } },
    };
    view.rerender(tree(Surface));
    expect(screen.getByRole("button", { name: "Reminders" })).toBeTruthy();
    await act(async () => {});
    expect(updateConversationSettings).not.toHaveBeenCalled();
  });

  it("hides the icon for guests and while authentication is loading", async () => {
    auth = { ...auth, isLoading: true };
    const view = render(tree(Surface));
    expect(screen.queryByRole("button", { name: "Reminders" })).toBeNull();
    auth = { ...auth, isLoading: false, isAuthenticated: false, user: null };
    view.rerender(tree(Surface));
    expect(screen.queryByRole("button", { name: "Reminders" })).toBeNull();
    await act(async () => {});
  });

  it("closes an open reminder panel immediately when disabled", async () => {
    const view = render(tree(Surface));
    fireEvent.click(screen.getByRole("button", { name: "Reminders" }));
    await screen.findByRole("dialog", { name: "Reminders" });
    auth = {
      ...auth,
      user: { ...auth.user, feature_preferences: { reminders: false } },
    };
    view.rerender(tree(Surface));
    expect(screen.queryByRole("dialog", { name: "Reminders" })).toBeNull();
    await act(async () => {});
  });
});

it("availability defaults show search/deep controls without activating either chat setting", async () => {
  render(tree(ChatModal));
  await waitFor(() => expect(getConversationSettings).toHaveBeenCalled());
  expect(
    screen.getByTestId("web-search-toggle").getAttribute("aria-pressed"),
  ).toBe("false");
  expect(
    screen.getByTestId("reasoning-effort-toggle").getAttribute("aria-pressed"),
  ).toBe("false");
  expect(screen.queryByTestId("memory-panel-toggle")).toBeNull();
  expect(updateConversationSettings).not.toHaveBeenCalled();
});

it("Settings management remains available with reminders off and creation disabled", async () => {
  auth.user.feature_preferences = { reminders: false };
  render(<ReminderButton label="Manage reminders" />);
  fireEvent.click(screen.getByRole("button", { name: "Manage reminders" }));
  await screen.findByText(/Manage scheduled reminders here/);
  expect(listReminders).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: /Add reminder/ }).disabled).toBe(
    true,
  );
});
