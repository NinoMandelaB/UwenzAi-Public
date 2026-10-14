import { StrictMode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import UwenzAiDev from "./UwenzAiDev";
import { developerWorkspace, workspaceText } from "../i18n/developerWorkspace";
import { LEGAL_POLICY_VERSION } from "../i18n/legal";
import {
  DEFAULT_LIMITS,
  MAX_PROMPT_CHARS,
  boundedDisplay,
  connectionCode,
  estimateTokens,
  messageLimitError,
  workspaceLimits,
} from "./developerWorkspaceUtils";
import * as api from "../services/agentDevService";

const state = vi.hoisted(() => ({ auth: {}, language: "en" }));
vi.mock("../context/AuthContext", () => ({ useAuth: () => state.auth }));
vi.mock("../context/LanguageContext", () => ({
  useLanguage: () => ({ language: state.language }),
}));
vi.mock("../services/agentDevService", () => ({
  listAgentKeys: vi.fn(),
  createAgentKey: vi.fn(),
  revokeAgentKey: vi.fn(),
  updateAgentKeyBudget: vi.fn(),
  getAgentUsage: vi.fn(),
  getConsent: vi.fn(),
  confirmConsent: vi.fn(),
  getAgentPricing: vi.fn(),
  playgroundCompletion: vi.fn(),
  getPlaygroundHistory: vi.fn(),
  deletePlaygroundHistory: vi.fn(),
}));
const tx = workspaceText("en");
const ok = (data) => ({ status: "ok", data });
const consentData = (confirmed) =>
  ok({ confirmed, version: LEGAL_POLICY_VERSION });
const key = {
  id: "key-1",
  label: "Development app",
  hint: "uw…abcd",
  is_active: true,
  is_expired: false,
  created_at: "2026-09-01T12:00:00Z",
};
const model = (name, provider, extra = {}) => ({
  model: name,
  provider,
  available: true,
  input_kes_per_1m: 14.567,
  output_kes_per_1m: 61.245,
  sparks_per_1m_input: 146,
  sparks_per_1m_output: 613,
  capabilities: [],
  ...extra,
});
const pricing = {
  models: [
    model("alpha", "provider-a", { markup_percent: 11.5 }),
    model("alpha", "provider-b", { markup_percent: 6 }),
    model("beta", "provider-b", { markup_percent: 6 }),
    model("gamma", "provider-c", { markup_percent: 10 }),
    model("offline", "provider-c", {
      available: false,
      markup_percent: 10,
    }),
  ],
  providers: {
    "provider-a": { country: "Austria (EU)", kind: "gateway" },
    "provider-b": { country: "France (EU)", kind: "inference" },
    "provider-c": { country: "Kenya", kind: "inference" },
  },
  base_url: "https://gateway.example.test/api/v1/agent",
  markup_percent: 11.5,
  spark_value_kes: 0.1,
  playground_limits: DEFAULT_LIMITS,
};
// The shape the API served before provider metadata and per-model markups.
const legacyPricing = JSON.parse(JSON.stringify(pricing));
legacyPricing.models.forEach((entry) => delete entry.markup_percent);
delete legacyPricing.providers;
const usage = {
  usage: {
    total_calls: 5,
    total_sparks: 21,
    total_input_tokens: 125,
    total_output_tokens: 80,
    monthly_calls: 2,
    monthly_sparks: 9,
  },
  by_model: [
    {
      model: "alpha",
      provider: "provider-a",
      total_input_tokens: 125,
      total_output_tokens: 80,
      total_calls: 5,
      total_sparks: 21,
      monthly_input_tokens: 40,
      monthly_output_tokens: 20,
      monthly_calls: 2,
      monthly_sparks: 9,
    },
  ],
};
const reply = (content = "Hello from the model", extra = {}) => ({
  choices: [{ message: { role: "assistant", content } }],
  usage: { prompt_tokens: 12, completion_tokens: 9 },
  _uwenzai: { sparks_charged: 1, ...extra },
});
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function choosePair(name = "alpha", provider = "provider-a") {
  fireEvent.change(screen.getByRole("combobox", { name: tx("model") }), {
    target: { value: name },
  });
  fireEvent.change(screen.getByRole("combobox", { name: tx("provider") }), {
    target: { value: provider },
  });
}
async function ready() {
  const view = render(<UwenzAiDev />);
  await screen.findByText(key.label);
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: tx("model") }).disabled).toBe(
      false,
    ),
  );
  choosePair();
  fireEvent.change(screen.getByRole("combobox", { name: tx("keySelect") }), {
    target: { value: key.id },
  });
  return view;
}
function prompt(value = "Hello") {
  fireEvent.change(screen.getByRole("textbox", { name: tx("prompt") }), {
    target: { value },
  });
}
function send() {
  fireEvent.click(
    screen.getByRole("button", { name: tx("send"), exact: true }),
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  state.auth = {
    user: { user_id: "user-1", username: "Amina" },
    accessToken: "session-access",
    isAuthenticated: true,
    isLoading: false,
    sessionGeneration: 1,
  };
  state.language = "en";
  api.getConsent.mockResolvedValue(consentData(true));
  api.confirmConsent.mockResolvedValue(consentData(true));
  api.getAgentPricing.mockResolvedValue(ok(pricing));
  api.listAgentKeys.mockResolvedValue(ok({ keys: [key] }));
  api.getAgentUsage.mockResolvedValue(ok(usage));
  api.createAgentKey.mockResolvedValue(
    ok({ key_id: "key-2", full_key: "one-time-test-value" }),
  );
  api.revokeAgentKey.mockResolvedValue(ok({ message: "revoked" }));
  api.playgroundCompletion.mockResolvedValue(reply());
  api.getPlaygroundHistory.mockResolvedValue(ok({ messages: [] }));
  api.deletePlaygroundHistory.mockResolvedValue(ok({ deleted: true }));
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});
afterEach(cleanup);

describe("authentication and consent", () => {
  it.each(["en", "sw"])(
    "presents updated %s documents and billing before explicit acceptance",
    async (language) => {
      state.language = language;
      api.getConsent.mockResolvedValue(consentData(false));
      render(<UwenzAiDev />);
      const text = workspaceText(language);
      await screen.findByText(text("consentBody"));
      expect(screen.getByText(text("consentCost"))).toBeTruthy();
      expect(
        screen.getByText(
          text("consentVersion", { version: LEGAL_POLICY_VERSION }),
          { exact: false },
        ),
      ).toBeTruthy();
      for (const [label, href] of [
        ["termsOfUse", "/terms"],
        ["privacyPolicy", "/privacy"],
        ["providerDisclosure", "/subprocessors"],
      ]) {
        const link = screen.getByRole("link", { name: text(label) });
        expect(link.getAttribute("href")).toBe(href);
        expect(link.getAttribute("target")).toBe("_blank");
        expect(link.getAttribute("rel")).toBe("noopener noreferrer");
      }
      expect(api.confirmConsent).not.toHaveBeenCalled();
      expect(api.listAgentKeys).not.toHaveBeenCalled();
      fireEvent.click(screen.getByText(text("consentAccept")));
      await screen.findByText(key.label);
      expect(api.confirmConsent).toHaveBeenCalledTimes(1);
    },
  );
  it.each([undefined, "1.0", "2026-09-11", "2099-01-01"])(
    "never unlocks for a confirmed GET with unbundled version %s",
    async (version) => {
      api.getConsent.mockResolvedValue(ok({ confirmed: true, version }));
      render(<UwenzAiDev />);
      await screen.findByText(tx("consentVersionMismatch"));
      expect(api.listAgentKeys).not.toHaveBeenCalled();
      expect(api.getAgentUsage).not.toHaveBeenCalled();
      expect(api.confirmConsent).not.toHaveBeenCalled();
      expect(screen.queryByRole("textbox", { name: tx("prompt") })).toBeNull();
    },
  );
  it.each([undefined, "1.0", "2099-01-01"])(
    "never unlocks for a successful POST with unbundled version %s",
    async (version) => {
      api.getConsent.mockResolvedValue(consentData(false));
      api.confirmConsent.mockResolvedValue(ok({ confirmed: true, version }));
      render(<UwenzAiDev />);
      fireEvent.click(await screen.findByText(tx("consentAccept")));
      await screen.findByText(tx("consentVersionMismatch"));
      expect(api.listAgentKeys).not.toHaveBeenCalled();
      expect(api.getAgentUsage).not.toHaveBeenCalled();
      expect(api.confirmConsent).toHaveBeenCalledTimes(1);
    },
  );
  it("requires refresh and review when the server version changes during acceptance", async () => {
    api.getConsent.mockResolvedValue(consentData(false));
    api.confirmConsent.mockRejectedValue(
      Object.assign(new Error("Notice changed"), {
        code: "consent_version_mismatch",
        status: 409,
      }),
    );
    render(<UwenzAiDev />);
    fireEvent.click(await screen.findByText(tx("consentAccept")));
    await screen.findByText(tx("consentVersionMismatch"));
    expect(api.confirmConsent).toHaveBeenCalledTimes(1);
    expect(api.listAgentKeys).not.toHaveBeenCalled();
    expect(api.getAgentUsage).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: tx("prompt") })).toBeNull();
  });
  it("waits for real auth hydration without false login popups or requests", async () => {
    state.auth = {
      ...state.auth,
      isLoading: true,
      accessToken: null,
      isAuthenticated: false,
      user: null,
    };
    const view = render(<UwenzAiDev />);
    expect(screen.getByRole("status").textContent).toContain(
      tx("authenticating"),
    );
    expect(api.getConsent).not.toHaveBeenCalled();
    expect(api.listAgentKeys).not.toHaveBeenCalled();
    expect(screen.queryByText(tx("login"))).toBeNull();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    state.auth = {
      user: { user_id: "user-1" },
      accessToken: "restored",
      isAuthenticated: true,
      isLoading: false,
      sessionGeneration: 2,
    };
    view.rerender(<UwenzAiDev />);
    await screen.findByText(key.label);
    expect(api.getConsent).toHaveBeenCalledTimes(1);
  });
  it("renders a settled signed-out state without fetching or opening a popup", () => {
    state.auth = {
      isLoading: false,
      isAuthenticated: false,
      user: null,
      accessToken: null,
    };
    render(<UwenzAiDev />);
    expect(screen.getByText(tx("login"))).toBeTruthy();
    expect(api.getAgentPricing).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
  it("survives StrictMode setup/cleanup without getting stuck in consent", async () => {
    render(
      <StrictMode>
        <UwenzAiDev />
      </StrictMode>,
    );
    await screen.findByText(key.label);
    expect(screen.queryByText(tx("consentChecking"))).toBeNull();
  });
  it("does not load keys or usage until consent is confirmed", async () => {
    const waiting = deferred();
    api.getConsent.mockReturnValue(waiting.promise);
    render(<UwenzAiDev />);
    await screen.findByText(tx("consentChecking"));
    expect(api.listAgentKeys).not.toHaveBeenCalled();
    expect(api.getAgentUsage).not.toHaveBeenCalled();
    await act(async () => waiting.resolve(consentData(false)));
    expect(screen.getByText(tx("consentBody"))).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: tx("prompt") })).toBeNull();
    fireEvent.click(screen.getByText(tx("consentAccept")));
    await screen.findByText(key.label);
    expect(api.confirmConsent).toHaveBeenCalledTimes(1);
  });
  it("fails closed for non-ok consent confirmation and allows inline retry", async () => {
    api.getConsent.mockResolvedValue(consentData(false));
    api.confirmConsent.mockResolvedValue({
      status: "error",
      data: { confirmed: true, version: LEGAL_POLICY_VERSION },
    });
    render(<UwenzAiDev />);
    fireEvent.click(await screen.findByText(tx("consentAccept")));
    await screen.findByText(tx("consentFailed"));
    expect(api.listAgentKeys).not.toHaveBeenCalled();
    expect(api.playgroundCompletion).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    api.getConsent.mockResolvedValue(consentData(true));
    fireEvent.click(screen.getByText(tx("retry")));
    await screen.findByText(key.label);
  });
  it("shows consent read failures inline without stacked modals", async () => {
    api.getConsent.mockRejectedValue(new Error("offline"));
    render(<UwenzAiDev />);
    await screen.findByText(tx("consentFailed"));
    expect(api.listAgentKeys).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
  it("ignores old consent and keys when switching accounts", async () => {
    const old = deferred();
    api.getConsent.mockReturnValueOnce(old.promise);
    const view = render(<UwenzAiDev />);
    const signal = api.getConsent.mock.calls[0][0].signal;
    state.auth = {
      ...state.auth,
      user: { user_id: "user-2" },
      sessionGeneration: 2,
    };
    api.getConsent.mockResolvedValue(consentData(false));
    view.rerender(<UwenzAiDev />);
    await screen.findByText(tx("consentAccept"));
    await act(async () => old.resolve(consentData(true)));
    expect(signal.aborted).toBe(true);
    expect(api.listAgentKeys).not.toHaveBeenCalled();
  });
  it("retains a prompt through token refresh and language changes without refetching", async () => {
    const view = await ready();
    prompt("keep this draft");
    state.auth = { ...state.auth, accessToken: "rotated" };
    state.language = "sw";
    view.rerender(<UwenzAiDev />);
    expect(
      screen.getByRole("textbox", { name: workspaceText("sw")("prompt") })
        .value,
    ).toBe("keep this draft");
    expect(api.getConsent).toHaveBeenCalledTimes(1);
    expect(api.listAgentKeys).toHaveBeenCalledTimes(1);
  });
});

describe("linked models, pricing and configuration", () => {
  it("keeps exactly one connection panel through repeated pair changes", async () => {
    await ready();
    for (const provider of ["provider-b", "provider-a", "provider-b"]) {
      fireEvent.change(screen.getByRole("combobox", { name: tx("provider") }), {
        target: { value: provider },
      });
      expect(
        screen.getAllByRole("heading", { name: tx("connection") }),
      ).toHaveLength(1);
      expect(
        screen.getAllByRole("button", {
          name: `${tx("copy")} ${tx("baseUrl")}`,
          exact: true,
        }),
      ).toHaveLength(1);
    }
  });
  it("links the API documentation site from the connection panel", async () => {
    const view = await ready();
    const link = screen.getByRole("link", { name: tx("docsLink") });
    expect(link.getAttribute("href")).toBe(
      "https://uwenzai.com/uwenzai-dev/docs/",
    );
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    state.language = "sw";
    view.rerender(<UwenzAiDev />);
    expect(
      screen
        .getByRole("link", { name: workspaceText("sw")("docsLink") })
        .getAttribute("href"),
    ).toBe("https://uwenzai.com/uwenzai-dev/docs/");
  });
  it("filters providers by model and models by provider with explicit resets", async () => {
    await ready();
    expect(
      [...screen.getByRole("combobox", { name: tx("provider") }).options].map(
        (o) => o.value,
      ),
    ).toEqual(["", "provider-a", "provider-b"]);
    expect(
      [...screen.getByRole("combobox", { name: tx("model") }).options].map(
        (o) => o.value,
      ),
    ).toEqual(["", "alpha"]);
    fireEvent.click(screen.getByText(tx("reset")));
    fireEvent.change(screen.getByRole("combobox", { name: tx("provider") }), {
      target: { value: "provider-b" },
    });
    expect(
      [...screen.getByRole("combobox", { name: tx("model") }).options].map(
        (o) => o.value,
      ),
    ).toEqual(["", "alpha", "beta"]);
    fireEvent.change(screen.getByRole("combobox", { name: tx("model") }), {
      target: { value: "beta" },
    });
    expect(screen.getByRole("combobox", { name: tx("provider") }).value).toBe(
      "provider-b",
    );
    fireEvent.change(screen.getByRole("combobox", { name: tx("model") }), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: tx("provider") }), {
      target: { value: "provider-c" },
    });
    expect(
      within(screen.getByRole("combobox", { name: tx("model") })).getByRole(
        "option",
        { name: /offline/ },
      ).disabled,
    ).toBe(true);
  });
  it("leads with Sparks prices, keeps KES secondary, markup footnote and honest unknown metadata", async () => {
    await ready();
    expect(screen.getByTestId("input-sparks-price").textContent.trim()).toBe(
      "146 Sparks",
    );
    expect(screen.getByTestId("output-sparks-price").textContent.trim()).toBe(
      "613 Sparks",
    );
    expect(screen.getByTestId("input-kes-price").textContent.trim()).toBe(
      "KES 14.57",
    );
    expect(screen.getByTestId("output-kes-price").textContent.trim()).toBe(
      "KES 61.25",
    );
    expect(screen.getByText(/Includes the 11.5% markup/)).toBeTruthy();
    expect(screen.getAllByText(tx("notSpecified")).length).toBeGreaterThan(0);
    expect(screen.getByText(pricing.base_url)).toBeTruthy();
  });
  it("labels provider options with country and kind from the pricing metadata", async () => {
    await ready();
    const providerSelect = screen.getByRole("combobox", {
      name: tx("provider"),
    });
    expect(
      within(providerSelect).getByRole("option", {
        name: "provider-a — Austria (EU) · Gateway",
      }).value,
    ).toBe("provider-a");
    expect(
      within(providerSelect).getByRole("option", {
        name: "provider-b — France (EU) · Inference",
      }).value,
    ).toBe("provider-b");
  });
  it("shows the selected model's markup instead of the global default", async () => {
    await ready();
    expect(screen.getByText(/Includes the 11\.5% markup/)).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox", { name: tx("provider") }), {
      target: { value: "provider-b" },
    });
    expect(screen.getByText(/Includes the 6\.0% markup/)).toBeTruthy();
    expect(screen.queryByText(/11\.5% markup/)).toBeNull();
  });
  it("renders pre-metadata pricing without crashing and keeps the global markup", async () => {
    api.getAgentPricing.mockResolvedValue(ok(legacyPricing));
    await ready();
    const providerSelect = screen.getByRole("combobox", {
      name: tx("provider"),
    });
    expect(
      within(providerSelect).getByRole("option", { name: "provider-a" }).value,
    ).toBe("provider-a");
    expect(screen.getByText(/Includes the 11\.5% markup/)).toBeTruthy();
  });
  it("copies authoritative baseURL, model/provider, and safe OpenAI config", async () => {
    await ready();
    for (const [name, value] of [
      ["baseUrl", pricing.base_url],
      ["model", "alpha"],
      ["provider", "provider-a"],
    ]) {
      fireEvent.click(
        screen.getByRole("button", {
          name: `${tx("copy")} ${tx(name)}`,
          exact: true,
        }),
      );
      await waitFor(() =>
        expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith(value),
      );
    }
    fireEvent.click(screen.getByRole("button", { name: tx("copyConfig") }));
    await waitFor(() =>
      expect(navigator.clipboard.writeText.mock.calls.at(-1)[0]).toContain(
        'extra_body={"provider": "provider-a"}',
      ),
    );
    expect(navigator.clipboard.writeText.mock.calls.at(-1)[0]).toContain(
      'os.environ["UWENZAI_KEY"]',
    );
    fireEvent.click(
      screen.getByRole("button", { name: tx("curl"), exact: true }),
    );
    fireEvent.click(screen.getByRole("button", { name: tx("copyConfig") }));
    await waitFor(() =>
      expect(navigator.clipboard.writeText.mock.calls.at(-1)[0]).toContain(
        '"provider": "provider-a"',
      ),
    );
    expect(navigator.clipboard.writeText.mock.calls.at(-1)[0]).toContain(
      "Bearer $UWENZAI_KEY",
    );
  });
  it("makes copy failures visible and keeps pricing failures retryable", async () => {
    api.getAgentPricing.mockRejectedValueOnce(new Error("offline"));
    render(<UwenzAiDev />);
    await screen.findByText(tx("pricingFailed"));
    fireEvent.click(screen.getByText(tx("retry")));
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: tx("model") }).disabled).toBe(
        false,
      ),
    );
    choosePair();
    navigator.clipboard.writeText.mockRejectedValue(new Error("denied"));
    fireEvent.click(screen.getByRole("button", { name: tx("copyConfig") }));
    await screen.findByText(tx("copyFailed"));
  });
  it("displays no-model state without leaving an infinite spinner", async () => {
    api.getAgentPricing.mockResolvedValue(ok({ ...pricing, models: [] }));
    render(<UwenzAiDev />);
    await screen.findByText(tx("emptyModels"));
    expect(screen.getByRole("combobox", { name: tx("model") }).disabled).toBe(
      true,
    );
  });
});

describe("keys", () => {
  it("creates, masks, reveals, copies, then clears a one-time key on pair change", async () => {
    await ready();
    fireEvent.click(screen.getByRole("button", { name: tx("newKey") }));
    fireEvent.change(screen.getByRole("textbox", { name: tx("keyLabel") }), {
      target: { value: "New app" },
    });
    api.listAgentKeys.mockResolvedValue(
      ok({ keys: [key, { ...key, id: "key-2", label: "New app" }] }),
    );
    fireEvent.submit(
      screen.getByRole("textbox", { name: tx("keyLabel") }).closest("form"),
    );
    await screen.findByText(tx("keyWarning"));
    expect(api.createAgentKey).toHaveBeenCalledWith(
      { label: "New app" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(screen.getByLabelText(tx("fullKey")).type).toBe("password");
    fireEvent.click(screen.getByText(tx("reveal")));
    expect(screen.getByLabelText(tx("fullKey")).value).toBe(
      "one-time-test-value",
    );
    fireEvent.click(
      screen.getByRole("button", { name: tx("copyKey"), exact: true }),
    );
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith(
        "one-time-test-value",
      ),
    );
    expect(screen.getByRole("combobox", { name: tx("keySelect") }).value).toBe(
      "key-2",
    );
    fireEvent.change(screen.getByRole("combobox", { name: tx("provider") }), {
      target: { value: "provider-b" },
    });
    expect(screen.queryByLabelText(tx("fullKey"))).toBeNull();
    expect(screen.getByRole("combobox", { name: tx("keySelect") }).value).toBe(
      "key-2",
    );
  });
  it("requires an inline confirmation to revoke and reports mutation errors", async () => {
    await ready();
    fireEvent.click(
      screen.getByRole("button", { name: `${tx("revoke")} ${key.label}` }),
    );
    expect(api.revokeAgentKey).not.toHaveBeenCalled();
    expect(screen.getByText(tx("revokeConfirm"))).toBeTruthy();
    api.revokeAgentKey.mockResolvedValueOnce({ status: "error", data: {} });
    fireEvent.click(
      screen.getByRole("button", { name: tx("revoke"), exact: true }),
    );
    await screen.findByText(tx("revokeFailed"));
    api.listAgentKeys.mockResolvedValue(
      ok({ keys: [{ ...key, is_active: false }] }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: tx("revoke"), exact: true }),
    );
    await screen.findByText(tx("keyRevoked"));
    expect(screen.getByRole("combobox", { name: tx("keySelect") }).value).toBe(
      "",
    );
  });
  it("creates a key with an optional monthly budget", async () => {
    await ready();
    fireEvent.click(screen.getByRole("button", { name: tx("newKey") }));
    const labelInput = screen.getByRole("textbox", { name: tx("keyLabel") });
    fireEvent.change(labelInput, { target: { value: "Budgeted app" } });
    fireEvent.change(screen.getByRole("textbox", { name: tx("budgetLabel") }), {
      target: { value: "250" },
    });
    api.createAgentKey.mockResolvedValueOnce(
      ok({ key_id: "key-9", full_key: "one-time-value" }),
    );
    api.listAgentKeys.mockResolvedValue(
      ok({
        keys: [
          {
            ...key,
            id: "key-9",
            label: "Budgeted app",
            monthly_spark_budget: 250,
          },
        ],
      }),
    );
    fireEvent.submit(labelInput.closest("form"));
    await screen.findByLabelText(tx("fullKey"));
    expect(api.createAgentKey).toHaveBeenCalledWith(
      { label: "Budgeted app", monthly_spark_budget: 250 },
      expect.anything(),
    );
    // The create form closes on success; the budget field does not persist open.
    expect(
      screen.queryByRole("textbox", { name: tx("budgetLabel") }),
    ).toBeNull();
  });
  it("edits a key budget inline and validates before saving", async () => {
    await ready();
    fireEvent.click(
      screen.getByRole("button", { name: `${tx("budgetEdit")} ${key.label}` }),
    );
    const input = screen.getByRole("textbox", { name: tx("budgetLabel") });
    expect(input.value).toBe("");
    // Reject an out-of-range value without calling the API.
    fireEvent.change(input, { target: { value: "0" } });
    fireEvent.click(
      screen.getByRole("button", { name: tx("budgetSave"), exact: true }),
    );
    expect(screen.getByText(tx("budgetInvalid"))).toBeTruthy();
    expect(api.updateAgentKeyBudget).not.toHaveBeenCalled();
    // A valid value goes through; failures surface, success reloads.
    fireEvent.change(input, { target: { value: "500" } });
    api.updateAgentKeyBudget.mockResolvedValueOnce({
      status: "error",
      data: {},
    });
    fireEvent.click(
      screen.getByRole("button", { name: tx("budgetSave"), exact: true }),
    );
    await screen.findByText(tx("budgetFailed"));
    api.updateAgentKeyBudget.mockResolvedValueOnce(
      ok({ key_id: key.id, monthly_spark_budget: 500 }),
    );
    api.listAgentKeys.mockResolvedValue(
      ok({ keys: [{ ...key, monthly_spark_budget: 500 }] }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: tx("budgetSave"), exact: true }),
    );
    await screen.findByText(tx("budgetSaved"));
    expect(api.updateAgentKeyBudget).toHaveBeenLastCalledWith(
      key.id,
      500,
      expect.anything(),
    );
  });
  it("keeps list/create failures visible and does not pretend an empty key list", async () => {
    api.listAgentKeys.mockRejectedValueOnce(new Error("offline"));
    render(<UwenzAiDev />);
    await screen.findByText(tx("keyFailed"));
    expect(screen.queryByText(tx("keyEmpty"))).toBeNull();
    fireEvent.click(screen.getByText(tx("retry")));
    await screen.findByText(key.label);
    fireEvent.click(screen.getByRole("button", { name: tx("newKey") }));
    const input = screen.getByRole("textbox", { name: tx("keyLabel") });
    fireEvent.submit(input.closest("form"));
    expect(screen.getByText(tx("keyLabelRequired"))).toBeTruthy();
    fireEvent.change(input, { target: { value: "My app" } });
    api.createAgentKey.mockResolvedValue({ status: "error", data: {} });
    fireEvent.submit(input.closest("form"));
    await screen.findByText(tx("createFailed"));
  });
  it("drops a late created secret on logout and never stores it locally", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const pending = deferred();
    const view = await ready();
    api.createAgentKey.mockReturnValue(pending.promise);
    fireEvent.click(screen.getByRole("button", { name: tx("newKey") }));
    const input = screen.getByRole("textbox", { name: tx("keyLabel") });
    fireEvent.change(input, { target: { value: "Old account" } });
    fireEvent.submit(input.closest("form"));
    state.auth = { ...state.auth, isAuthenticated: false, user: null };
    view.rerender(<UwenzAiDev />);
    await act(async () =>
      pending.resolve(
        ok({ key_id: "late", full_key: "secret-from-old-session" }),
      ),
    );
    expect(screen.queryByLabelText(tx("fullKey"))).toBeNull();
    expect(storage).not.toHaveBeenCalled();
    storage.mockRestore();
  });
});

describe("billed playground", () => {
  it("recognizes a settled replay with no choices, refreshes usage and does not retry forever", async () => {
    await ready();
    prompt("Previously billed prompt");
    api.playgroundCompletion.mockRejectedValueOnce({ code: "timeout" });
    send();
    await screen.findByText(tx("timeout"));
    const originalId =
      api.playgroundCompletion.mock.calls[0][0].client_operation_id;
    api.playgroundCompletion.mockResolvedValueOnce({
      choices: [],
      usage: { prompt_tokens: 12, completion_tokens: 9 },
      _uwenzai: {
        idempotent_retry: true,
        sparks_charged: 0,
        original_sparks_charged: 7,
      },
    });
    send();
    await screen.findByText(tx("completedReplay"));
    expect(api.playgroundCompletion.mock.calls[1][0].client_operation_id).toBe(
      originalId,
    );
    expect(api.getAgentUsage).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("textbox", { name: tx("prompt") }).value).toBe("");
    expect(screen.queryByText(tx("invalidResponse"))).toBeNull();
    expect(screen.getByText("7")).toBeTruthy();
    prompt("Previously billed prompt");
    send();
    await screen.findByText("Hello from the model");
    expect(
      api.playgroundCompletion.mock.calls[2][0].client_operation_id,
    ).not.toBe(originalId);
  });
  it("offers a new paid attempt only after explicit terminal and refund confirmation", async () => {
    await ready();
    prompt("Retry after a refund");
    api.playgroundCompletion.mockRejectedValueOnce({
      code: "upstream_error",
      status: 502,
      terminal: true,
      refund_confirmed: true,
    });
    send();
    await screen.findByText(tx("refundedFailed"));
    const originalId =
      api.playgroundCompletion.mock.calls[0][0].client_operation_id;
    expect(api.playgroundCompletion).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: tx("newPaidAttempt") }));
    await screen.findByText("Hello from the model");
    expect(
      api.playgroundCompletion.mock.calls[1][0].client_operation_id,
    ).not.toBe(originalId);
  });
  it("never mints a new operation for refund-pending or unconfirmed upstream failures", async () => {
    await ready();
    prompt("Uncertain charge");
    api.playgroundCompletion.mockRejectedValueOnce({
      code: "refund_pending",
      status: 503,
      terminal: false,
      refund_confirmed: false,
    });
    send();
    await screen.findByText(tx("refundPending"));
    const originalId =
      api.playgroundCompletion.mock.calls[0][0].client_operation_id;
    expect(
      screen.queryByRole("button", { name: tx("newPaidAttempt") }),
    ).toBeNull();
    api.playgroundCompletion.mockRejectedValueOnce({
      code: "upstream_error",
      status: 502,
      terminal: true,
      refund_confirmed: false,
    });
    send();
    await screen.findByText(tx("sendFailed"));
    expect(api.playgroundCompletion.mock.calls[1][0].client_operation_id).toBe(
      originalId,
    );
    expect(
      screen.queryByRole("button", { name: tx("newPaidAttempt") }),
    ).toBeNull();
  });
  it("sends only the latest turn with history off and refreshes metered usage", async () => {
    await ready();
    expect(screen.getByRole("checkbox", { name: tx("history") }).checked).toBe(
      false,
    );
    expect(screen.getByText(tx("billNotice"))).toBeTruthy();
    prompt("First prompt");
    send();
    await screen.findByText("Hello from the model");
    expect(api.playgroundCompletion.mock.calls[0][0]).toEqual({
      key_id: "key-1",
      model: "alpha",
      provider: "provider-a",
      messages: [{ role: "user", content: "First prompt" }],
      max_tokens: 1024,
      save_history: false,
      client_operation_id: expect.stringMatching(/^[a-f0-9-]{36}$/),
    });
    expect(api.getAgentUsage).toHaveBeenCalledTimes(2);
    prompt("Next prompt");
    api.playgroundCompletion.mockResolvedValue(reply("Second reply"));
    send();
    await screen.findByText("Second reply");
    expect(api.playgroundCompletion.mock.calls[1][0].messages).toEqual([
      { role: "user", content: "Next prompt" },
    ]);
    expect(screen.getByText("First prompt")).toBeTruthy();
    expect(screen.getByText("Next prompt")).toBeTruthy();
  });
  it("renders model output as literal text instead of HTML", async () => {
    api.playgroundCompletion.mockResolvedValue(
      reply('<img src=x onerror="alert(1)"><script>bad()</script>'),
    );
    await ready();
    prompt();
    send();
    await screen.findByText(
      '<img src=x onerror="alert(1)"><script>bad()</script>',
    );
    expect(screen.queryByRole("img", { name: "" })).toBeNull();
    expect(document.querySelector(".dev-message img")).toBeNull();
  });
  it("disables concurrent sends and ignores replies after stop; retries reuse the operation ID", async () => {
    const pending = deferred();
    api.playgroundCompletion.mockReturnValueOnce(pending.promise);
    await ready();
    prompt("A billed request");
    send();
    expect(screen.getByRole("button", { name: tx("stop") })).toBeTruthy();
    fireEvent.submit(
      screen.getByRole("textbox", { name: tx("prompt") }).closest("form"),
    );
    expect(api.playgroundCompletion).toHaveBeenCalledTimes(1);
    const originalId =
      api.playgroundCompletion.mock.calls[0][0].client_operation_id;
    const signal = api.playgroundCompletion.mock.calls[0][1].signal;
    fireEvent.click(screen.getByRole("button", { name: tx("stop") }));
    expect(signal.aborted).toBe(true);
    await screen.findByText(tx("stopped"));
    await act(async () => pending.resolve(reply("Late ignored reply")));
    expect(screen.queryByText("Late ignored reply")).toBeNull();
    send();
    await screen.findByText("Hello from the model");
    expect(api.playgroundCompletion.mock.calls[1][0].client_operation_id).toBe(
      originalId,
    );
  });
  it("preserves prompts after non-ok errors, maps billing errors and never spins forever", async () => {
    api.playgroundCompletion.mockRejectedValueOnce({ status: 402 });
    await ready();
    prompt("Keep my prompt");
    send();
    await screen.findByText(tx("insufficient"));
    expect(screen.getByRole("textbox", { name: tx("prompt") }).value).toBe(
      "Keep my prompt",
    );
    const operation =
      api.playgroundCompletion.mock.calls[0][0].client_operation_id;
    api.playgroundCompletion.mockResolvedValueOnce({
      status: "error",
      error: { message: "Failure" },
    });
    send();
    await screen.findByText(tx("sendFailed"));
    expect(api.playgroundCompletion.mock.calls[1][0].client_operation_id).toBe(
      operation,
    );
    expect(
      screen.getByRole("button", { name: tx("send"), exact: true }).disabled,
    ).toBe(false);
  });
  it("clears chat on pair/account changes and ignores late responses", async () => {
    const pending = deferred();
    api.playgroundCompletion.mockReturnValueOnce(pending.promise);
    const view = await ready();
    prompt("private prompt");
    send();
    fireEvent.change(screen.getByRole("combobox", { name: tx("provider") }), {
      target: { value: "provider-b" },
    });
    expect(screen.getByRole("textbox", { name: tx("prompt") }).value).toBe("");
    await act(async () => pending.resolve(reply("private late answer")));
    expect(screen.queryByText("private late answer")).toBeNull();
    expect(api.getAgentUsage).toHaveBeenCalledTimes(1);
    state.auth = {
      ...state.auth,
      user: { user_id: "new-user" },
      sessionGeneration: 2,
    };
    view.rerender(<UwenzAiDev />);
    await screen.findByText(key.label);
    expect(screen.getByRole("combobox", { name: tx("keySelect") }).value).toBe(
      "",
    );
  });
  it("rejects oversized new prompts explicitly without silent truncation", async () => {
    await ready();
    prompt("draft");
    prompt("x".repeat(MAX_PROMPT_CHARS + 1));
    expect(screen.getByText(tx("promptTooLong"))).toBeTruthy();
    expect(screen.getByRole("textbox", { name: tx("prompt") }).value).toBe(
      "draft",
    );
    expect(api.playgroundCompletion).not.toHaveBeenCalled();
    prompt("字".repeat(12000));
    expect(
      screen.getByRole("button", { name: tx("send"), exact: true }).disabled,
    ).toBe(true);
    expect(screen.getAllByText(tx("promptTooLong")).length).toBeGreaterThan(0);
  });
  it("bounds response tokens with the server and model caps", async () => {
    api.getAgentPricing.mockResolvedValue(
      ok({
        ...pricing,
        models: [model("alpha", "provider-a", { max_output_tokens: 128 })],
      }),
    );
    await ready();
    const max = screen.getByRole("spinbutton", { name: tx("maxTokens") });
    expect(max.value).toBe("128");
    prompt();
    fireEvent.change(max, { target: { value: "129" } });
    expect(
      screen.getByRole("button", { name: tx("send"), exact: true }).disabled,
    ).toBe(true);
    fireEvent.change(max, { target: { value: "64" } });
    send();
    await screen.findByText("Hello from the model");
    expect(api.playgroundCompletion.mock.calls[0][0].max_tokens).toBe(64);
  });
});

describe("opt-in server history", () => {
  it.each(["en", "sw"])(
    "shows the approved %s history notice before opting in and while saving",
    async (language) => {
      const view = await ready();
      state.language = language;
      view.rerender(<UwenzAiDev />);
      const text = workspaceText(language);
      const checkbox = screen.getByRole("checkbox", { name: text("history") });
      expect(checkbox.checked).toBe(false);
      const notice = document.getElementById(
        checkbox.getAttribute("aria-describedby"),
      );
      expect(notice.textContent).toBe(text("historyNotice"));
      expect(
        within(notice)
          .getByRole("link", { name: text("privacyPolicy") })
          .getAttribute("href"),
      ).toBe("/privacy");
      fireEvent.click(checkbox);
      await waitFor(() => expect(api.getPlaygroundHistory).toHaveBeenCalled());
      expect(notice.textContent).toBe(text("historyNotice"));
      expect(checkbox.checked).toBe(true);
    },
  );
  it("loads the selected pair and submits previous plus current messages only after loading", async () => {
    const history = deferred();
    api.getPlaygroundHistory.mockReturnValueOnce(history.promise);
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: tx("history") }));
    prompt("New turn");
    expect(
      screen.getByRole("button", { name: tx("send"), exact: true }).disabled,
    ).toBe(true);
    const messages = [
      { role: "user", content: "Saved question" },
      { role: "assistant", content: "Saved answer" },
    ];
    await act(async () => history.resolve(ok({ messages })));
    await screen.findByText("Saved answer");
    send();
    await screen.findByText("Hello from the model");
    expect(api.playgroundCompletion.mock.calls[0][0].messages).toEqual([
      ...messages,
      { role: "user", content: "New turn" },
    ]);
    expect(api.playgroundCompletion.mock.calls[0][0].save_history).toBe(true);
    expect(api.getPlaygroundHistory.mock.calls[0][0]).toMatchObject({
      model: "alpha",
      provider: "provider-a",
    });
  });
  it("turning history off deletes the saved pair; failure blocks send until retry succeeds", async () => {
    api.getPlaygroundHistory.mockResolvedValue(
      ok({ messages: [{ role: "user", content: "Saved" }] }),
    );
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: tx("history") }));
    await screen.findByText("Saved");
    api.deletePlaygroundHistory.mockRejectedValueOnce(new Error("offline"));
    fireEvent.click(screen.getByRole("checkbox", { name: tx("history") }));
    await screen.findByText(tx("deleteFailed"));
    prompt("Keep prompt");
    expect(
      screen.getByRole("button", { name: tx("send"), exact: true }).disabled,
    ).toBe(true);
    expect(screen.getByRole("checkbox", { name: tx("history") }).checked).toBe(
      true,
    );
    fireEvent.click(screen.getByText(tx("retry")));
    await screen.findByText(tx("deleted"));
    expect(screen.getByRole("checkbox", { name: tx("history") }).checked).toBe(
      false,
    );
    expect(screen.queryByText("Saved")).toBeNull();
    expect(screen.getByRole("textbox", { name: tx("prompt") }).value).toBe(
      "Keep prompt",
    );
  });
  it("cancels pending history loads on delete so they cannot restore cleared messages", async () => {
    const history = deferred();
    api.getPlaygroundHistory.mockReturnValueOnce(history.promise);
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: tx("history") }));
    fireEvent.click(screen.getByRole("button", { name: tx("deleteHistory") }));
    await screen.findByText(tx("deleted"));
    await act(async () =>
      history.resolve(
        ok({ messages: [{ role: "user", content: "Late history" }] }),
      ),
    );
    expect(screen.queryByText("Late history")).toBeNull();
    expect(api.getPlaygroundHistory.mock.calls[0][1].signal.aborted).toBe(true);
  });
  it("prevents toggles/deletion while a saved-history send is pending", async () => {
    const pending = deferred();
    api.playgroundCompletion.mockReturnValueOnce(pending.promise);
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: tx("history") }));
    await waitFor(() =>
      expect(screen.queryByText(tx("historyLoading"))).toBeNull(),
    );
    prompt();
    send();
    expect(screen.getByRole("checkbox", { name: tx("history") }).disabled).toBe(
      true,
    );
    expect(
      screen.getByRole("button", { name: tx("deleteHistory") }).disabled,
    ).toBe(true);
    expect(screen.queryByRole("button", { name: tx("stop") })).toBeNull();
    await act(async () => pending.resolve(reply()));
    expect(screen.getByRole("checkbox", { name: tx("history") }).disabled).toBe(
      false,
    );
  });
  it("bounds history and never trims a new prompt to fit", async () => {
    api.getPlaygroundHistory.mockResolvedValue(
      ok({
        messages: Array.from({ length: 60 }, (_, i) => ({
          role: i % 2 ? "assistant" : "user",
          content: `Message ${i}`,
        })),
      }),
    );
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: tx("history") }));
    await screen.findByText("Message 59");
    prompt("New prompt must stay whole");
    expect(screen.getByText(tx("historyTooLong"))).toBeTruthy();
    expect(
      screen.getByRole("button", { name: tx("send"), exact: true }).disabled,
    ).toBe(true);
    expect(screen.getByRole("textbox", { name: tx("prompt") }).value).toBe(
      "New prompt must stay whole",
    );
  });
  it("shows a warning, not a failed send, if a billed reply could not be saved", async () => {
    api.playgroundCompletion.mockResolvedValue(
      reply("Billed reply", {
        history_saved: false,
        history_warning: "not saved",
      }),
    );
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: tx("history") }));
    await waitFor(() =>
      expect(screen.queryByText(tx("historyLoading"))).toBeNull(),
    );
    prompt();
    send();
    await screen.findByText("Billed reply");
    expect(screen.getByText(tx("historySaveWarning"))).toBeTruthy();
    expect(screen.getByRole("textbox", { name: tx("prompt") }).value).toBe("");
    expect(api.playgroundCompletion).toHaveBeenCalledTimes(1);
  });
  it("isolates saved history after switching providers", async () => {
    const pending = deferred();
    api.getPlaygroundHistory.mockReturnValueOnce(pending.promise);
    await ready();
    fireEvent.click(screen.getByRole("checkbox", { name: tx("history") }));
    fireEvent.change(screen.getByRole("combobox", { name: tx("provider") }), {
      target: { value: "provider-b" },
    });
    await act(async () =>
      pending.resolve(
        ok({ messages: [{ role: "user", content: "Old pair" }] }),
      ),
    );
    expect(screen.queryByText("Old pair")).toBeNull();
    expect(screen.getByRole("checkbox", { name: tx("history") }).checked).toBe(
      false,
    );
  });
});

describe("usage and languages", () => {
  it("shows grouped month/all-time totals and keeps last request attributed after pair changes", async () => {
    await ready();
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("40")).toHaveLength(2);
    fireEvent.click(
      screen.getByRole("button", { name: tx("allTime"), exact: true }),
    );
    expect(within(table).getAllByText("125").length).toBe(2);
    prompt();
    send();
    await screen.findByText("Hello from the model");
    expect(screen.getByText("alpha / provider-a")).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox", { name: tx("provider") }), {
      target: { value: "provider-b" },
    });
    expect(screen.getByText("alpha / provider-a")).toBeTruthy();
  });
  it("breaks usage down by key and source, playground and direct API", async () => {
    api.getAgentUsage.mockResolvedValue(
      ok({
        ...usage,
        by_key: [
          {
            key_id: key.id,
            label: key.label,
            hint: "abcd",
            is_active: true,
            removed: false,
            source: "playground",
            monthly_calls: 1,
            monthly_sparks: 3,
            total_calls: 1,
            total_sparks: 3,
          },
          {
            key_id: key.id,
            label: key.label,
            hint: "abcd",
            is_active: true,
            removed: false,
            source: "api",
            monthly_calls: 2,
            monthly_sparks: 7,
            total_calls: 2,
            total_sparks: 7,
          },
          {
            key_id: null,
            label: "",
            hint: "zz99",
            is_active: false,
            removed: true,
            source: "api",
            monthly_calls: 0,
            monthly_sparks: 0,
            total_calls: 4,
            total_sparks: 11,
          },
        ],
      }),
    );
    // ready() looks the key label up by unique text; the by-key table now
    // repeats it, so wait for the model selector readiness directly.
    render(<UwenzAiDev />);
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: tx("model") }).disabled).toBe(
        false,
      ),
    );
    await screen.findByText(tx("byKey"));
    const tables = screen.getAllByRole("table");
    const byKeyTable = tables[tables.length - 1];
    expect(screen.getByText(tx("usageNote"))).toBeTruthy();
    expect(
      within(byKeyTable).getAllByText(tx("sourcePlayground")),
    ).toHaveLength(1);
    expect(within(byKeyTable).getAllByText(tx("sourceApi"))).toHaveLength(2);
    expect(within(byKeyTable).getByText(tx("removedKey"))).toBeTruthy();
    expect(within(byKeyTable).getAllByText(key.label)).toHaveLength(2);
  });
  it("keeps usable content while reporting a usage refresh failure", async () => {
    await ready();
    api.getAgentUsage.mockRejectedValueOnce(new Error("offline"));
    fireEvent.click(screen.getByRole("button", { name: tx("refresh") }));
    await screen.findByText(tx("usageFailed"));
    expect(screen.getByRole("table")).toBeTruthy();
    expect(
      screen.getByRole("combobox", { name: tx("keySelect") }),
    ).toBeTruthy();
  });
  it("renders Swahili and has matching translation coverage", async () => {
    state.language = "sw";
    render(<UwenzAiDev />);
    await screen.findByText(key.label);
    const sw = workspaceText("sw");
    expect(screen.getByRole("combobox", { name: sw("model") })).toBeTruthy();
    expect(screen.getByText(sw("billNotice"))).toBeTruthy();
    expect(Object.keys(developerWorkspace.en).sort()).toEqual(
      Object.keys(developerWorkspace.sw).sort(),
    );
  });
  it("bounds utility budgets and produces provider-aware config for smaller models", () => {
    expect(
      workspaceLimits({
        playground_limits: { max_messages: 80, max_output_tokens: 4000 },
      }).max_output_tokens,
    ).toBe(1024);
    expect(
      connectionCode("curl", pricing.base_url, {
        ...pricing.models[0],
        max_output_tokens: 32,
      }),
    ).toContain('"max_tokens": 32');
    expect(
      connectionCode("python", pricing.base_url, {
        ...pricing.models[0],
        max_output_tokens: 32,
      }),
    ).toContain("max_tokens=32");
    expect(
      messageLimitError(
        [{ role: "user", content: "字".repeat(12000) }],
        DEFAULT_LIMITS,
      ),
    ).toBe("historyTooLong");
    expect(
      estimateTokens([{ role: "user", content: "Hello" }]),
    ).toBeGreaterThan(0);
    expect(
      boundedDisplay(
        Array.from({ length: 80 }, () => ({
          role: "user",
          content: "x".repeat(2000),
        })),
      ).length,
    ).toBeLessThan(60);
  });
});
