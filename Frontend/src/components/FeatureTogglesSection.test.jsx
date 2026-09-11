import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import FeatureTogglesSection from "./FeatureTogglesSection";
import { useState, useCallback } from "react";

// Stable identity (like the real LanguageContext, which memoizes t).
const stableT = (path) => path;

vi.mock("../context/LanguageContext", () => ({
  useLanguage: () => ({ language: "en", t: stableT }),
}));

const mockSetUser = vi.fn();

vi.mock("../context/AuthContext", () => ({
  useAuth: () => {
    const [prefs, setPrefs] = useState({
      reminders: false,
      deepthink: true,
      websearch: false,
      memory_editor: false,
    });
    return {
      user: {
        user_id: "u-1",
        feature_preferences: prefs,
      },
      setUser: mockSetUser,
      syncFeaturePreferences: useCallback(async (updates, options) => {
        const data =
          updates === undefined
            ? await getFeaturePreferences(options)
            : await updateFeaturePreferences(updates, options);
        if (data?.data?.feature_preferences) {
          if (updates)
            mockSetUser({ feature_preferences: data.data.feature_preferences });
          setPrefs(data.data.feature_preferences);
        }
        return data?.data?.feature_preferences;
      }, []),
    };
  },
}));

vi.mock("../services/authService", () => ({
  getFeaturePreferences: vi.fn(),
  updateFeaturePreferences: vi.fn(),
}));

const { getFeaturePreferences, updateFeaturePreferences } =
  await import("../services/authService");

beforeEach(() => {
  vi.clearAllMocks();
  getFeaturePreferences.mockResolvedValue({
    status: "success",
    data: {
      feature_preferences: {
        reminders: false,
        deepthink: true,
        websearch: false,
        memory_editor: false,
      },
    },
  });
});

describe("FeatureTogglesSection", () => {
  it("shows absent defaults without PATCHing or turning on per-chat features", async () => {
    getFeaturePreferences.mockResolvedValue({
      data: { feature_preferences: {} },
    });
    render(<FeatureTogglesSection />);
    await waitFor(() =>
      expect(screen.getByLabelText("settings.feature_reminders").disabled).toBe(
        false,
      ),
    );
    for (const key of ["reminders", "deepthink", "websearch"])
      expect(
        screen
          .getByLabelText(`settings.feature_${key}`)
          .getAttribute("aria-pressed"),
      ).toBe("true");
    expect(
      screen
        .getByLabelText("settings.feature_memory_editor")
        .getAttribute("aria-pressed"),
    ).toBe("false");
    expect(updateFeaturePreferences).not.toHaveBeenCalled();
  });

  it("preserves a server's explicit false for all availability settings", async () => {
    getFeaturePreferences.mockResolvedValue({
      data: {
        feature_preferences: {
          reminders: false,
          deepthink: false,
          websearch: false,
          memory_editor: false,
        },
      },
    });
    render(<FeatureTogglesSection />);
    await waitFor(() =>
      expect(screen.getByLabelText("settings.feature_reminders").disabled).toBe(
        false,
      ),
    );
    for (const key of ["reminders", "deepthink", "websearch", "memory_editor"])
      expect(
        screen
          .getByLabelText(`settings.feature_${key}`)
          .getAttribute("aria-pressed"),
      ).toBe("false");
    expect(updateFeaturePreferences).not.toHaveBeenCalled();
  });

  it("renders a row per feature with the initial state", async () => {
    render(<FeatureTogglesSection />);

    expect(await screen.findByText("settings.feature_reminders")).toBeTruthy();
    expect(screen.getByText("settings.feature_deepthink")).toBeTruthy();
    expect(screen.getByText("settings.feature_websearch")).toBeTruthy();
    expect(screen.getByText("settings.feature_memory_editor")).toBeTruthy();

    // deepthink starts on (from the auth context snapshot), the rest off.
    expect(
      screen
        .getByLabelText("settings.feature_deepthink")
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen
        .getByLabelText("settings.feature_reminders")
        .getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("toggles a feature and syncs the shared preference action", async () => {
    updateFeaturePreferences.mockResolvedValue({
      status: "success",
      data: {
        feature_preferences: {
          reminders: true,
          deepthink: true,
          websearch: false,
          memory_editor: false,
        },
      },
    });
    render(<FeatureTogglesSection />);

    const toggle = await screen.findByLabelText("settings.feature_reminders");
    // Rows stay disabled until the initial load resolves — wait for that,
    // otherwise the click can race the loader and silently do nothing.
    await waitFor(() => expect(toggle.disabled).toBe(false));
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(updateFeaturePreferences).toHaveBeenCalledWith(
        {
          reminders: true,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
    // The auth context is updated so the chat UI reflects the change live.
    await waitFor(() => {
      expect(mockSetUser).toHaveBeenCalledWith(
        expect.objectContaining({
          feature_preferences: expect.objectContaining({ reminders: true }),
        }),
      );
    });
    expect(
      screen
        .getByLabelText("settings.feature_reminders")
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("reverts the toggle and shows an error when saving fails", async () => {
    updateFeaturePreferences.mockRejectedValue(new Error("boom"));
    render(<FeatureTogglesSection />);

    const toggle = await screen.findByLabelText("settings.feature_websearch");
    // Rows stay disabled until the initial load resolves — wait for that,
    // otherwise the click can race the loader and silently do nothing.
    await waitFor(() => expect(toggle.disabled).toBe(false));
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByText("settings.featureToggleError")).toBeTruthy();
    });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(mockSetUser).not.toHaveBeenCalled();
  });
});
