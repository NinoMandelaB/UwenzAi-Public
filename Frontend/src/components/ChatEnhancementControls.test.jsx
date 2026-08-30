import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatEnhancementControls } from "./ChatModal";

function renderControls(overrides = {}) {
  const props = {
    activeConvId: "conversation-1",
    canUseConversationEnhancements: false,
    isTogglingSettings: false,
    webSearchEnabled: false,
    reasoningEffort: "fast",
    onToggleWebSearch: vi.fn(),
    onToggleReasoningEffort: vi.fn(),
    ...overrides,
  };
  render(<ChatEnhancementControls {...props} />);
  return props;
}

describe("ChatEnhancementControls", () => {
  it("visibly disables web search and deep reasoning for guests", () => {
    const props = renderControls();

    const searchToggle = screen.getByTestId("web-search-toggle");
    const reasoningToggle = screen.getByTestId("reasoning-effort-toggle");

    expect(searchToggle.disabled).toBe(true);
    expect(reasoningToggle.disabled).toBe(true);
    expect(searchToggle.getAttribute("title")).toBe(
      "Web search and deep thinking are available to logged-in users.",
    );

    fireEvent.click(searchToggle);
    fireEvent.click(reasoningToggle);
    expect(props.onToggleWebSearch).not.toHaveBeenCalled();
    expect(props.onToggleReasoningEffort).not.toHaveBeenCalled();
  });

  it("enables both controls for logged-in users", () => {
    const props = renderControls({ canUseConversationEnhancements: true });

    const searchToggle = screen.getByTestId("web-search-toggle");
    const reasoningToggle = screen.getByTestId("reasoning-effort-toggle");

    expect(searchToggle.disabled).toBe(false);
    expect(reasoningToggle.disabled).toBe(false);

    fireEvent.click(searchToggle);
    fireEvent.click(reasoningToggle);
    expect(props.onToggleWebSearch).toHaveBeenCalledOnce();
    expect(props.onToggleReasoningEffort).toHaveBeenCalledOnce();
  });
});
