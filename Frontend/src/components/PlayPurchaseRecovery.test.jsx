import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ auth: {} }));
vi.mock("../context/AuthContext", () => ({ useAuth: () => state.auth }));
vi.mock("../services/playBillingService", () => ({
  recoverPendingPlayPurchases: vi.fn(async () => ({ recovered: 0 })),
}));

import PlayPurchaseRecovery from "./PlayPurchaseRecovery";
import { recoverPendingPlayPurchases } from "../services/playBillingService";

const SIGNED_IN = {
  isLoading: false,
  isAuthenticated: true,
  user: { user_id: "user-1" },
  accessToken: "token-1",
};

describe("PlayPurchaseRecovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.auth = { ...SIGNED_IN };
  });

  afterEach(cleanup);

  it("recovers Play purchases when the app opens with a signed-in account", () => {
    render(<PlayPurchaseRecovery />);
    expect(recoverPendingPlayPurchases).toHaveBeenCalledTimes(1);
  });

  it("waits until the auth state is known", () => {
    state.auth = { ...SIGNED_IN, isLoading: true };
    const { rerender } = render(<PlayPurchaseRecovery />);
    expect(recoverPendingPlayPurchases).not.toHaveBeenCalled();

    state.auth = { ...SIGNED_IN };
    rerender(<PlayPurchaseRecovery />);
    expect(recoverPendingPlayPurchases).toHaveBeenCalledTimes(1);
  });

  it("does nothing for guests, whose purchases the backend refuses", () => {
    state.auth = {
      isLoading: false,
      isAuthenticated: false,
      user: null,
      accessToken: null,
    };
    render(<PlayPurchaseRecovery />);
    expect(recoverPendingPlayPurchases).not.toHaveBeenCalled();
  });

  it("does not run again when only the access token is refreshed", () => {
    const { rerender } = render(<PlayPurchaseRecovery />);
    state.auth = { ...SIGNED_IN, accessToken: "token-2" };
    rerender(<PlayPurchaseRecovery />);
    expect(recoverPendingPlayPurchases).toHaveBeenCalledTimes(1);
  });

  it("runs for the next account after switching users", () => {
    const { rerender } = render(<PlayPurchaseRecovery />);
    state.auth = { ...SIGNED_IN, user: { user_id: "user-2" } };
    rerender(<PlayPurchaseRecovery />);
    expect(recoverPendingPlayPurchases).toHaveBeenCalledTimes(2);
  });

  it("renders nothing", () => {
    const { container } = render(<PlayPurchaseRecovery />);
    expect(container.innerHTML).toBe("");
  });
});
