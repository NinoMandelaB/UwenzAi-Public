import { describe, it, expect, afterEach, vi } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import { usePWAInstall } from "./usePWAInstall";

function fireBeforeInstallPrompt({ outcome = "accepted" } = {}) {
  const event = new Event("beforeinstallprompt");
  event.prompt = vi.fn();
  event.userChoice = Promise.resolve({ outcome });
  window.dispatchEvent(event);
  return event;
}

afterEach(cleanup);

describe("usePWAInstall", () => {
  it("is not installable before the browser fires beforeinstallprompt", () => {
    const { result } = renderHook(() => usePWAInstall());

    expect(result.current.canInstall).toBe(false);
    expect(result.current.isInstalled).toBe(false);
  });

  it("becomes installable once beforeinstallprompt fires", () => {
    const { result } = renderHook(() => usePWAInstall());

    act(() => {
      fireBeforeInstallPrompt();
    });

    expect(result.current.canInstall).toBe(true);
  });

  it("consumes the deferred prompt on promptInstall", async () => {
    const { result } = renderHook(() => usePWAInstall());

    let event;
    act(() => {
      event = fireBeforeInstallPrompt({ outcome: "accepted" });
    });

    let outcome;
    await act(async () => {
      outcome = await result.current.promptInstall();
    });

    expect(event.prompt).toHaveBeenCalledOnce();
    expect(outcome).toBe("accepted");
    // The event is single-use: no second prompt without a fresh event.
    expect(result.current.canInstall).toBe(false);
  });

  it("stays installable-hidden after appinstalled even with a stashed event", () => {
    const { result } = renderHook(() => usePWAInstall());

    act(() => {
      fireBeforeInstallPrompt();
    });
    act(() => {
      window.dispatchEvent(new Event("appinstalled"));
    });

    expect(result.current.isInstalled).toBe(true);
    expect(result.current.canInstall).toBe(false);
  });
});
