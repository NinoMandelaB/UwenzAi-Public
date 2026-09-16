import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import MemoryButton, { MemoryPanel } from "./MemoryPanel";

vi.mock("../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));
vi.mock("../context/AuthContext", () => ({
  useAuth: () => scope,
}));
let scope = { user: { user_id: "u-1" }, sessionGeneration: 1 };

// Stable identity (like the real LanguageContext, which memoizes t) — a new
// function per render would re-trigger the panel's load effect every render.
const stableT = (path, vars = {}) => {
  let result = path;
  Object.entries(vars).forEach(([k, v]) => {
    result = result.replace(`{${k}}`, v);
  });
  return result;
};

vi.mock("../context/LanguageContext", () => ({
  useLanguage: () => ({
    language: "en",
    t: stableT,
  }),
}));

vi.mock("../services/memoryService", () => ({
  listMemory: vi.fn(),
  createMemoryFact: vi.fn(),
  updateMemoryFact: vi.fn(),
  deleteMemoryFact: vi.fn(),
  createMemoryRelation: vi.fn(),
  updateMemoryRelation: vi.fn(),
  deleteMemoryRelation: vi.fn(),
  updateMemorySummary: vi.fn(),
  deleteMemorySummary: vi.fn(),
}));

const {
  listMemory,
  createMemoryFact,
  createMemoryRelation,
  deleteMemoryFact,
  updateMemoryFact,
  updateMemorySummary,
} = await import("../services/memoryService");

const sampleData = {
  facts: [
    {
      memory_fact_id: "f-1",
      fact_text: "I work as a teacher in Mombasa",
      fact_type: "personal",
      importance_score: 7,
      confidence_score: 1,
      shared: false,
      extracted_at: "2026-09-01T10:00:00+00:00",
      valid_from: null,
    },
    {
      memory_fact_id: "f-2",
      fact_text: "I have two brothers",
      fact_type: "personal",
      importance_score: 5,
      confidence_score: 0.9,
      shared: true,
      extracted_at: "2026-09-02T10:00:00+00:00",
      valid_from: null,
    },
  ],
  relations: [
    {
      memory_relation_id: "r-1",
      subject: "my sister",
      predicate: "works_at",
      object: "Safaricom",
      shared: false,
      extracted_at: "2026-09-01T10:00:00+00:00",
    },
  ],
  summary: {
    summary_id: "s-1",
    summary_text: "We talked about teaching and family.",
    created_at: "2026-09-01T10:00:00+00:00",
    updated_at: "2026-09-01T10:00:00+00:00",
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  scope = { user: { user_id: "u-1" }, sessionGeneration: 1 };
});

describe("MemoryButton", () => {
  it("renders a toggle button and opens the panel on click", async () => {
    listMemory.mockResolvedValue(sampleData);
    render(<MemoryButton characterId="c-1" charName="Zuri" />);

    expect(screen.queryByRole("dialog")).toBe(null);

    fireEvent.click(screen.getByTestId("memory-panel-toggle"));

    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(listMemory).toHaveBeenCalledWith("c-1");
  });
});

describe("MemoryPanel", () => {
  it.each(["character", "account", "session"])(
    "isolates reversed reads across a %s change",
    async (change) => {
      let resolveA;
      listMemory
        .mockReturnValueOnce(
          new Promise((r) => {
            resolveA = r;
          }),
        )
        .mockResolvedValue({
          facts: [{ memory_fact_id: "B", fact_text: "B memory" }],
        });
      const view = render(<MemoryPanel characterId="c-1" charName="A" />);
      if (change === "account")
        scope = { user: { user_id: "u-2" }, sessionGeneration: 2 };
      if (change === "session")
        scope = { user: { user_id: "u-1" }, sessionGeneration: 2 };
      view.rerender(
        <MemoryPanel
          characterId={change === "character" ? "c-2" : "c-1"}
          charName="B"
        />,
      );
      expect(await screen.findByText("B memory")).toBeTruthy();
      await act(async () => resolveA(sampleData));
      expect(screen.queryByText(sampleData.facts[0].fact_text)).toBe(null);
      expect(screen.getByText("B memory")).toBeTruthy();
    },
  );

  it("discards an old character's summary draft and pending mutation", async () => {
    let resolveSave;
    listMemory.mockResolvedValueOnce(sampleData).mockResolvedValue({
      summary: {
        ...sampleData.summary,
        summary_id: "s-2",
        summary_text: "B summary",
      },
    });
    updateMemorySummary.mockReturnValue(
      new Promise((r) => {
        resolveSave = r;
      }),
    );
    const view = render(<MemoryPanel characterId="c-1" />);
    const summary = await screen.findByRole("region", {
      name: "memory.summary.title",
    });
    fireEvent.click(within(summary).getByLabelText("memory.edit"));
    fireEvent.change(
      screen.getByDisplayValue(sampleData.summary.summary_text),
      { target: { value: "A draft" } },
    );
    fireEvent.click(screen.getByText("memory.save"));
    view.rerender(<MemoryPanel characterId="c-2" />);
    expect(await screen.findByText("B summary")).toBeTruthy();
    expect(screen.queryByDisplayValue("A draft")).toBe(null);
    await act(async () =>
      resolveSave({ ...sampleData.summary, summary_text: "A draft" }),
    );
    expect(screen.getByText("B summary")).toBeTruthy();
    expect(screen.queryByText("A draft")).toBe(null);
    expect(updateMemorySummary).toHaveBeenCalledTimes(1);
    expect(updateMemorySummary.mock.calls[0][0]).toBe("c-1");
  });

  it("preserves the draft on stale edit and sends snapshot preconditions", async () => {
    listMemory.mockResolvedValue(sampleData);
    updateMemorySummary.mockRejectedValue(
      Object.assign(new Error("stale_memory"), { status: 409 }),
    );
    render(<MemoryPanel characterId="c-1" />);
    const summary = await screen.findByRole("region", {
      name: "memory.summary.title",
    });
    fireEvent.click(within(summary).getByLabelText("memory.edit"));
    fireEvent.change(
      screen.getByDisplayValue(sampleData.summary.summary_text),
      { target: { value: "My correction" } },
    );
    fireEvent.click(screen.getByText("memory.save"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByDisplayValue("My correction")).toBeTruthy();
    expect(updateMemorySummary).toHaveBeenCalledWith("c-1", "My correction", {
      expected_summary_id: "s-1",
      expected_updated_at: sampleData.summary.updated_at,
    });
    const latest = {
      ...sampleData.summary,
      summary_text: "New server summary",
      updated_at: "2026-09-10T11:00:00Z",
    };
    listMemory.mockResolvedValue({ ...sampleData, summary: latest });
    fireEvent.click(screen.getByText("memory.reloadLatest"));
    expect(
      await screen.findByText("memory.latestVersion: New server summary"),
    ).toBeTruthy();
    expect(screen.getByDisplayValue("My correction")).toBeTruthy();
    updateMemorySummary.mockResolvedValue({
      ...latest,
      summary_text: "My correction",
    });
    fireEvent.click(screen.getByText("memory.save"));
    await waitFor(() =>
      expect(updateMemorySummary).toHaveBeenLastCalledWith(
        "c-1",
        "My correction",
        {
          expected_summary_id: "s-1",
          expected_updated_at: latest.updated_at,
        },
      ),
    );
  });

  it("caps manual summary edits at 12000 Unicode code points without truncating stored text", async () => {
    listMemory.mockResolvedValue({
      summary: { ...sampleData.summary, summary_text: "x".repeat(12001) },
    });
    updateMemorySummary.mockResolvedValue({
      ...sampleData.summary,
      summary_text: "🦁".repeat(12000),
    });
    render(<MemoryPanel characterId="c-1" />);
    const summary = await screen.findByRole("region", {
      name: "memory.summary.title",
    });
    expect(within(summary).getByText("x".repeat(12001))).toBeTruthy();
    fireEvent.click(within(summary).getByLabelText("memory.edit"));
    expect(screen.getByText("memory.save").disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "🦁".repeat(12000) },
    });
    expect(screen.getByText("memory.save").disabled).toBe(false);
    fireEvent.click(screen.getByText("memory.save"));
    await waitFor(() =>
      expect(updateMemorySummary.mock.calls[0][1]).toHaveLength(24000),
    );
  });

  it("keeps a deleted summary's draft and never drops its stale precondition on reload", async () => {
    listMemory.mockResolvedValue(sampleData);
    updateMemorySummary.mockRejectedValue(
      Object.assign(new Error("stale_memory"), { status: 409 }),
    );
    render(<MemoryPanel characterId="c-1" />);
    const region = await screen.findByRole("region", {
      name: "memory.summary.title",
    });
    fireEvent.click(within(region).getByLabelText("memory.edit"));
    fireEvent.change(
      screen.getByDisplayValue(sampleData.summary.summary_text),
      { target: { value: "Do not lose my draft" } },
    );
    fireEvent.click(screen.getByText("memory.save"));
    await screen.findByRole("alert");
    listMemory.mockResolvedValue({ ...sampleData, summary: null });
    fireEvent.click(screen.getByText("memory.reloadLatest"));
    expect(await screen.findByText("memory.latestUnavailable")).toBeTruthy();
    expect(screen.getByDisplayValue("Do not lose my draft")).toBeTruthy();
    fireEvent.click(screen.getByText("memory.save"));
    await waitFor(() => expect(updateMemorySummary).toHaveBeenCalledTimes(2));
    expect(updateMemorySummary.mock.calls[1][2]).toEqual({
      expected_summary_id: "s-1",
      expected_updated_at: sampleData.summary.updated_at,
    });
  });

  it("deduplicates a manual-add 201 containing an existing fact", async () => {
    listMemory.mockResolvedValue(sampleData);
    createMemoryFact.mockResolvedValue(sampleData.facts[0]);
    render(<MemoryPanel characterId="c-1" />);
    fireEvent.click(await screen.findByText("memory.facts.add"));
    fireEvent.change(screen.getByPlaceholderText("memory.facts.placeholder"), {
      target: { value: sampleData.facts[0].fact_text },
    });
    fireEvent.click(screen.getByText("memory.add"));
    await waitFor(() => expect(createMemoryFact).toHaveBeenCalled());
    // Wait for the settled count instead of asserting synchronously: until the
    // form state clears, the add-fact textarea still mirrors the typed text
    // through its default content and matches getAllByText too. A real dedup
    // regression keeps the count at 2 forever, so this still fails then.
    await waitFor(() =>
      expect(screen.getAllByText(sampleData.facts[0].fact_text)).toHaveLength(
        1,
      ),
    );
  });

  it("sends the fact version captured when editing began", async () => {
    const fact = { ...sampleData.facts[0], updated_at: "2026-09-10T10:00:00Z" };
    listMemory.mockResolvedValue({
      facts: [fact],
      relations: [],
      summary: null,
    });
    updateMemoryFact.mockResolvedValue({
      ...fact,
      fact_text: "Corrected fact",
    });
    render(<MemoryPanel characterId="c-1" />);
    await screen.findByText(fact.fact_text);
    fireEvent.click(screen.getByLabelText("memory.edit"));
    fireEvent.change(screen.getByDisplayValue(fact.fact_text), {
      target: { value: "Corrected fact" },
    });
    fireEvent.click(screen.getByText("memory.save"));
    await waitFor(() =>
      expect(updateMemoryFact).toHaveBeenCalledWith(
        fact.memory_fact_id,
        "Corrected fact",
        {
          expected_updated_at: fact.updated_at,
        },
      ),
    );
  });

  it("retains the server's empty-object relation policy", async () => {
    listMemory.mockResolvedValue({ facts: [], relations: [], summary: null });
    createMemoryRelation.mockResolvedValue({
      memory_relation_id: "empty-object",
      subject: "me",
      predicate: "likes",
      object: "",
    });
    render(<MemoryPanel characterId="c-1" />);
    fireEvent.click(await screen.findByText("memory.relations.add"));
    fireEvent.change(
      screen.getByPlaceholderText("memory.relations.subjectPlaceholder"),
      { target: { value: "me" } },
    );
    fireEvent.change(
      screen.getByPlaceholderText("memory.relations.predicatePlaceholder"),
      { target: { value: "likes" } },
    );
    fireEvent.click(screen.getByText("memory.add"));
    await waitFor(() =>
      expect(createMemoryRelation).toHaveBeenCalledWith("c-1", {
        subject: "me",
        predicate: "likes",
        object: "",
      }),
    );
  });

  it("loads beyond 200 facts, retaining summary and independent relation metadata", async () => {
    listMemory.mockImplementation(async (_character, options) => {
      const page = options?.page || 1;
      return {
        facts: Array.from({ length: 50 }, (_, i) => ({
          memory_fact_id: `f${(page - 1) * 50 + i}`,
          fact_text: `Fact ${(page - 1) * 50 + i}`,
        })),
        relations: [],
        summary: sampleData.summary,
        pagination: {
          page,
          page_size: 50,
          facts: { total: 250, has_next: page < 5 },
          relations: { total: 0, has_next: false },
        },
      };
    });
    render(<MemoryPanel characterId="c-1" />);
    await screen.findByText("Fact 0");
    for (let page = 2; page <= 5; page += 1) {
      fireEvent.click(screen.getByText("memory.loadMore"));
      await screen.findByText(`Fact ${(page - 1) * 50}`);
    }
    expect(screen.getByText("Fact 249")).toBeTruthy();
    expect(screen.getByText(sampleData.summary.summary_text)).toBeTruthy();
    expect(screen.queryByText("memory.loadMore")).toBe(null);
    expect(listMemory).toHaveBeenLastCalledWith("c-1", {
      page: 5,
      pageSize: 50,
    });
  }, 15000); // Real 250-row DOM under shared CI CPU contention.

  it.each([
    { facts: [null] },
    { facts: [{ fact_text: "no id" }] },
    {
      relations: [
        { memory_relation_id: "r", subject: "me", predicate: 5, object: "" },
      ],
    },
    { summary: { summary_id: "s", summary_text: {} } },
  ])("shows recoverable load errors for malformed records", async (payload) => {
    listMemory.mockResolvedValue(payload);
    render(<MemoryPanel characterId="c-1" />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText("memory.retry")).toBeTruthy();
  });
  it("shows facts, relations and the summary", async () => {
    listMemory.mockResolvedValue(sampleData);
    render(<MemoryPanel characterId="c-1" charName="Zuri" onClose={vi.fn()} />);

    expect(
      await screen.findByText("I work as a teacher in Mombasa"),
    ).toBeTruthy();
    expect(screen.getByText(/my sister/)).toBeTruthy();
    expect(screen.getByText(/Safaricom/)).toBeTruthy();
    expect(
      screen.getByText("We talked about teaching and family."),
    ).toBeTruthy();
    // The shared (user-global) fact carries its badge.
    expect(screen.getByText("memory.shared")).toBeTruthy();
  });

  it("shows an empty state when there is nothing remembered", async () => {
    listMemory.mockResolvedValue({
      facts: [],
      relations: [],
      summary: null,
    });
    render(<MemoryPanel characterId="c-1" charName="Zuri" onClose={vi.fn()} />);

    expect(await screen.findByText("memory.facts.empty")).toBeTruthy();
    expect(screen.getByText("memory.relations.empty")).toBeTruthy();
    expect(screen.getByText("memory.summary.empty")).toBeTruthy();
  });

  it("adds a fact through the add form", async () => {
    listMemory.mockResolvedValue({ facts: [], relations: [], summary: null });
    createMemoryFact.mockResolvedValue({
      memory_fact_id: "f-new",
      fact_text: "I love pilau",
      shared: false,
    });
    render(<MemoryPanel characterId="c-1" charName="Zuri" onClose={vi.fn()} />);

    fireEvent.click(await screen.findByText("memory.facts.add"));
    fireEvent.change(
      await screen.findByPlaceholderText("memory.facts.placeholder"),
      {
        target: { value: "I love pilau" },
      },
    );
    fireEvent.click(screen.getByText("memory.add"));

    await waitFor(() => {
      expect(createMemoryFact).toHaveBeenCalledWith("c-1", "I love pilau");
    });
    expect(await screen.findByText("I love pilau")).toBeTruthy();
  });

  it("deletes a fact after inline confirmation", async () => {
    listMemory.mockResolvedValue(sampleData);
    deleteMemoryFact.mockResolvedValue({ memory_fact_id: "f-1" });
    render(<MemoryPanel characterId="c-1" charName="Zuri" onClose={vi.fn()} />);

    await screen.findByText("I work as a teacher in Mombasa");

    const deleteButtons = screen.getAllByLabelText("memory.delete");
    fireEvent.click(deleteButtons[0]);

    fireEvent.click(screen.getByText("memory.confirmDelete"));

    await waitFor(() => {
      expect(deleteMemoryFact).toHaveBeenCalledWith("f-1", {});
    });
    await waitFor(() => {
      expect(screen.queryByText("I work as a teacher in Mombasa")).toBe(null);
    });
  });

  it("edits the summary and saves it", async () => {
    listMemory.mockResolvedValue(sampleData);
    updateMemorySummary.mockResolvedValue({
      ...sampleData.summary,
      summary_text: "Edited summary.",
    });
    render(<MemoryPanel characterId="c-1" charName="Zuri" onClose={vi.fn()} />);

    await screen.findByText("We talked about teaching and family.");
    // Edit buttons in DOM order: fact 1, fact 2, relation, summary.
    fireEvent.click(screen.getAllByLabelText("memory.edit")[3]);

    const textarea = await screen.findByDisplayValue(
      "We talked about teaching and family.",
    );
    fireEvent.change(textarea, { target: { value: "Edited summary." } });
    fireEvent.click(screen.getByText("memory.save"));

    await waitFor(() => {
      expect(updateMemorySummary).toHaveBeenCalledWith(
        "c-1",
        "Edited summary.",
        {
          expected_summary_id: "s-1",
          expected_updated_at: sampleData.summary.updated_at,
        },
      );
    });
    expect(await screen.findByText("Edited summary.")).toBeTruthy();
  });

  it("shows a load error with retry", async () => {
    listMemory.mockRejectedValue(new Error("boom"));
    render(<MemoryPanel characterId="c-1" charName="Zuri" onClose={vi.fn()} />);

    expect(await screen.findByText("boom")).toBeTruthy();
    expect(screen.getByText("memory.retry")).toBeTruthy();

    listMemory.mockResolvedValue(sampleData);
    fireEvent.click(screen.getByText("memory.retry"));

    expect(
      await screen.findByText("I work as a teacher in Mombasa"),
    ).toBeTruthy();
  });
});
