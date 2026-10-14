// src/services/playBillingService.test.js
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./apiClient", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "./apiClient";
import { API } from "../config/constants";
import {
  PLAY_ERRORS,
  PlayBillingError,
  fetchPlayItemDetails,
  formatPlayPrice,
  getPlayBillingService,
  purchaseSparksViaPlay,
  recoverPendingPlayPurchases,
  resetPlayBillingServiceCache,
} from "./playBillingService";

function playService(overrides = {}) {
  return {
    getDetails: vi.fn(),
    listPurchases: vi.fn(),
    consume: vi.fn(),
    ...overrides,
  };
}

function apiResponse(body, ok = true) {
  return {
    ok,
    status: ok ? 200 : 400,
    json: async () => body,
  };
}

const CREDITED_BODY = apiResponse({
  status: "success",
  data: {
    result: "credited",
    sparks_granted: 1000,
    acknowledged: true,
    spark_pool_balance: 1000,
  },
});

describe("Play billing detection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetPlayBillingServiceCache();
    delete window.getDigitalGoodsService;
  });

  it("resolves to null when the Digital Goods API is absent (plain website)", async () => {
    expect(await getPlayBillingService()).toBeNull();
  });

  it("resolves to the Play service inside the app", async () => {
    const service = playService();
    window.getDigitalGoodsService = vi.fn(async () => service);
    expect(await getPlayBillingService()).toBe(service);
    expect(window.getDigitalGoodsService).toHaveBeenCalledWith(
      "https://play.google.com/billing",
    );
  });

  it("resolves to null when the Play provider rejects (browser, not app)", async () => {
    window.getDigitalGoodsService = vi.fn(async () => {
      throw new Error("Payment method not supported");
    });
    expect(await getPlayBillingService()).toBeNull();
  });

  it("caches the detection so repeated calls do not re-probe", async () => {
    const service = playService();
    const probe = vi.fn(async () => service);
    window.getDigitalGoodsService = probe;
    await getPlayBillingService();
    await getPlayBillingService();
    expect(probe).toHaveBeenCalledTimes(1);
  });
});

describe("fetchPlayItemDetails", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetPlayBillingServiceCache();
    delete window.getDigitalGoodsService;
  });

  it("maps Play details by product id", async () => {
    const service = playService({
      getDetails: vi.fn(async () => [
        {
          itemId: "sparks_small",
          title: "Starter",
          price: { currency: "KES", value: "120.00" },
        },
      ]),
    });
    window.getDigitalGoodsService = vi.fn(async () => service);

    const details = await fetchPlayItemDetails(["sparks_small"]);
    expect(details["sparks_small"]).toEqual({
      itemId: "sparks_small",
      title: "Starter",
      description: undefined,
      price: "120.00",
      currency: "KES",
      type: undefined,
    });
  });

  it("returns an empty map when details fail — never a Paystack fallback", async () => {
    const service = playService({
      getDetails: vi.fn(async () => {
        throw new Error("store unavailable");
      }),
    });
    window.getDigitalGoodsService = vi.fn(async () => service);
    expect(await fetchPlayItemDetails(["sparks_small"])).toEqual({});
  });
});

describe("formatPlayPrice", () => {
  it("formats a currency amount", () => {
    const formatted = formatPlayPrice("KES", "120.00");
    expect(formatted).toContain("120");
  });

  it("falls back to a plain label for unknown currencies", () => {
    expect(formatPlayPrice("ZZZ", "5")).toContain("5");
  });

  it("returns null without a currency or value", () => {
    expect(formatPlayPrice("", "5")).toBeNull();
    expect(formatPlayPrice("KES", "")).toBeNull();
  });
});

describe("purchaseSparksViaPlay", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetPlayBillingServiceCache();
    apiFetch.mockReset();
    delete window.getDigitalGoodsService;
  });

  it("uses service.buy() when the store provides it, then verifies with the backend", async () => {
    const service = playService({
      buy: vi.fn(async () => ({
        purchaseToken: "token-1",
        orderId: "GPA.3304-1234",
      })),
      consume: vi.fn(async () => {}),
    });
    apiFetch.mockResolvedValue(CREDITED_BODY);

    const outcome = await purchaseSparksViaPlay({
      service,
      productId: "sparks_small",
    });

    expect(service.buy).toHaveBeenCalledWith("sparks_small");
    expect(apiFetch).toHaveBeenCalledWith(API.PLAY_BILLING.VERIFY, {
      method: "POST",
      body: {
        product_id: "sparks_small",
        purchase_token: "token-1",
        order_id: "GPA.3304-1234",
      },
    });
    expect(service.consume).toHaveBeenCalledWith("token-1");
    expect(outcome).toEqual({
      result: "credited",
      sparksGranted: 1000,
      balance: 1000,
    });
  });

  it("falls back to the PaymentRequest flow and closes the sheet with success", async () => {
    const complete = vi.fn(async () => {});
    class MockPaymentRequest {
      constructor() {
        this.show = vi.fn(async () => ({
          details: { purchaseToken: "token-2" },
          complete,
        }));
      }
    }
    globalThis.PaymentRequest = MockPaymentRequest;
    const service = playService({ consume: vi.fn(async () => {}) });
    apiFetch.mockResolvedValue(CREDITED_BODY);

    const outcome = await purchaseSparksViaPlay({
      service,
      productId: "sparks_small",
    });

    expect(outcome.result).toBe("credited");
    expect(complete).toHaveBeenCalledWith("success");
    expect(service.consume).toHaveBeenCalledWith("token-2");
    delete globalThis.PaymentRequest;
  });

  it("accepts the legacy details.token spelling from older Chrome versions", async () => {
    const complete = vi.fn(async () => {});
    class MockPaymentRequest {
      constructor() {
        this.show = vi.fn(async () => ({
          details: { token: "token-legacy" },
          complete,
        }));
      }
    }
    globalThis.PaymentRequest = MockPaymentRequest;
    const service = playService();
    apiFetch.mockResolvedValue(CREDITED_BODY);

    await purchaseSparksViaPlay({ service, productId: "sparks_small" });

    expect(apiFetch.mock.calls[0][1].body.purchase_token).toBe("token-legacy");
    delete globalThis.PaymentRequest;
  });

  it("reports a dismissed Play sheet as a user cancellation, not a failure", async () => {
    class MockPaymentRequest {
      constructor() {
        this.show = vi.fn(async () => {
          throw new DOMException("aborted", "AbortError");
        });
      }
    }
    globalThis.PaymentRequest = MockPaymentRequest;
    const service = playService();

    await expect(
      purchaseSparksViaPlay({ service, productId: "sparks_small" }),
    ).rejects.toMatchObject({ code: PLAY_ERRORS.USER_CANCELLED });

    expect(apiFetch).not.toHaveBeenCalled();
    delete globalThis.PaymentRequest;
  });

  it("fails the sheet and never consumes when the backend rejects the token", async () => {
    const complete = vi.fn(async () => {});
    class MockPaymentRequest {
      constructor() {
        this.show = vi.fn(async () => ({
          details: { purchaseToken: "token-3" },
          complete,
        }));
      }
    }
    globalThis.PaymentRequest = MockPaymentRequest;
    const service = playService({ consume: vi.fn() });
    apiFetch.mockResolvedValue(
      apiResponse({ status: "fail", data: "purchase_not_found" }, false),
    );

    await expect(
      purchaseSparksViaPlay({ service, productId: "sparks_small" }),
    ).rejects.toMatchObject({ code: PLAY_ERRORS.VERIFY_FAILED });

    // The billing sheet must still close, but with failure — and no consume,
    // because no sparks were credited.
    expect(complete).toHaveBeenCalledWith("fail");
    expect(service.consume).not.toHaveBeenCalled();
    delete globalThis.PaymentRequest;
  });

  function mockPaymentSheet(purchaseToken, complete) {
    class MockPaymentRequest {
      constructor() {
        this.show = vi.fn(async () => ({
          details: { purchaseToken },
          complete,
        }));
      }
    }
    globalThis.PaymentRequest = MockPaymentRequest;
  }

  it("does not consume a pending purchase and leaves the sheet outcome unknown", async () => {
    const complete = vi.fn(async () => {});
    mockPaymentSheet("token-pending", complete);
    const service = playService({ consume: vi.fn(async () => {}) });
    apiFetch.mockResolvedValue(
      apiResponse({
        status: "success",
        data: { result: "pending", sparks_granted: 0 },
      }),
    );

    const outcome = await purchaseSparksViaPlay({
      service,
      productId: "sparks_small",
    });

    // Consuming now would discard a purchase Google may still complete; it
    // stays open for the recovery on the next app open.
    expect(outcome).toEqual({
      result: "pending",
      sparksGranted: 0,
      balance: null,
    });
    expect(service.consume).not.toHaveBeenCalled();
    expect(complete).toHaveBeenCalledWith("unknown");
    expect(complete).not.toHaveBeenCalledWith("success");
    delete globalThis.PaymentRequest;
  });

  it("does not consume a cancelled purchase and fails the sheet", async () => {
    const complete = vi.fn(async () => {});
    mockPaymentSheet("token-cancelled", complete);
    const service = playService({ consume: vi.fn(async () => {}) });
    apiFetch.mockResolvedValue(
      apiResponse({
        status: "success",
        data: { result: "cancelled", sparks_granted: 0 },
      }),
    );

    const outcome = await purchaseSparksViaPlay({
      service,
      productId: "sparks_small",
    });

    expect(outcome.result).toBe("cancelled");
    expect(service.consume).not.toHaveBeenCalled();
    expect(complete).toHaveBeenCalledWith("fail");
    delete globalThis.PaymentRequest;
  });

  it("consumes and closes with success when the purchase was already credited", async () => {
    const complete = vi.fn(async () => {});
    mockPaymentSheet("token-again", complete);
    const service = playService({ consume: vi.fn(async () => {}) });
    apiFetch.mockResolvedValue(
      apiResponse({
        status: "success",
        data: {
          result: "already_credited",
          sparks_granted: 1000,
          spark_pool_balance: 1000,
        },
      }),
    );

    const outcome = await purchaseSparksViaPlay({
      service,
      productId: "sparks_small",
    });

    expect(outcome.result).toBe("already_credited");
    expect(service.consume).toHaveBeenCalledWith("token-again");
    expect(complete).toHaveBeenCalledWith("success");
    delete globalThis.PaymentRequest;
  });

  it("always closes the sheet when the payment response carries no token", async () => {
    const complete = vi.fn(async () => {});
    class MockPaymentRequest {
      constructor() {
        this.show = vi.fn(async () => ({ details: {}, complete }));
      }
    }
    globalThis.PaymentRequest = MockPaymentRequest;
    const service = playService();

    await expect(
      purchaseSparksViaPlay({ service, productId: "sparks_small" }),
    ).rejects.toMatchObject({ code: PLAY_ERRORS.VERIFY_FAILED });
    expect(complete).toHaveBeenCalledWith("fail");
    delete globalThis.PaymentRequest;
  });
});

describe("recoverPendingPlayPurchases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetPlayBillingServiceCache();
    apiFetch.mockReset();
    delete window.getDigitalGoodsService;
  });

  it("verifies and consumes outstanding purchases idempotently", async () => {
    const service = playService({
      listPurchases: vi.fn(async () => [
        { itemId: "sparks_small", purchaseToken: "token-a" },
        { itemId: "sparks_medium", purchaseToken: "token-b" },
      ]),
      consume: vi.fn(async () => {}),
    });
    window.getDigitalGoodsService = vi.fn(async () => service);
    apiFetch.mockImplementation(async (_url, options) =>
      apiResponse({
        status: "success",
        data: {
          result:
            options.body.purchase_token === "token-a"
              ? "credited"
              : "already_credited",
          sparks_granted: 500,
          spark_pool_balance: 500,
        },
      }),
    );

    const result = await recoverPendingPlayPurchases();

    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(service.consume).toHaveBeenCalledWith("token-a");
    expect(service.consume).toHaveBeenCalledWith("token-b");
    expect(result).toEqual({ recovered: 2 });
  });

  it("skips tokens the backend refuses without breaking the sweep", async () => {
    const service = playService({
      listPurchases: vi.fn(async () => [
        { itemId: "sparks_small", purchaseToken: "token-bad" },
      ]),
      consume: vi.fn(),
    });
    window.getDigitalGoodsService = vi.fn(async () => service);
    apiFetch.mockResolvedValue(
      apiResponse({ status: "fail", data: "product_not_offered" }, false),
    );

    const result = await recoverPendingPlayPurchases();

    expect(result).toEqual({ recovered: 0 });
    expect(service.consume).not.toHaveBeenCalled();
  });

  it("is a no-op outside the app", async () => {
    expect(await recoverPendingPlayPurchases()).toEqual({ recovered: 0 });
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("leaves a still-pending purchase unconsumed for a later run", async () => {
    const service = playService({
      listPurchases: vi.fn(async () => [
        { itemId: "sparks_small", purchaseToken: "token-slow" },
      ]),
      consume: vi.fn(),
    });
    window.getDigitalGoodsService = vi.fn(async () => service);
    apiFetch.mockResolvedValue(
      apiResponse({
        status: "success",
        data: { result: "pending", sparks_granted: 0 },
      }),
    );

    expect(await recoverPendingPlayPurchases()).toEqual({ recovered: 0 });
    expect(service.consume).not.toHaveBeenCalled();
  });

  it("shares one run between overlapping callers (app open and Spark Pool page)", async () => {
    let releaseList;
    const service = playService({
      listPurchases: vi.fn(
        () =>
          new Promise((resolve) => {
            releaseList = () =>
              resolve([{ itemId: "sparks_small", purchaseToken: "token-a" }]);
          }),
      ),
      consume: vi.fn(async () => {}),
    });
    window.getDigitalGoodsService = vi.fn(async () => service);
    apiFetch.mockResolvedValue(CREDITED_BODY);

    const first = recoverPendingPlayPurchases();
    const second = recoverPendingPlayPurchases();
    expect(second).toBe(first);

    await vi.waitFor(() => expect(service.listPurchases).toHaveBeenCalled());
    releaseList();
    expect(await first).toEqual({ recovered: 1 });
    expect(await second).toEqual({ recovered: 1 });
    expect(service.listPurchases).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledTimes(1);

    // Once finished, the next app open runs again.
    service.listPurchases.mockResolvedValue([]);
    expect(await recoverPendingPlayPurchases()).toEqual({ recovered: 0 });
    expect(service.listPurchases).toHaveBeenCalledTimes(2);
  });
});
