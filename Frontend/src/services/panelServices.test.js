import { beforeEach, expect, it, vi } from "vitest";
import { apiFetch } from "./apiClient";
import {
  listMemory,
  updateMemoryFact,
  updateMemorySummary,
} from "./memoryService";
import { listReminders, updateReminder } from "./reminderService";
vi.mock("./apiClient", () => ({ apiFetch: vi.fn() }));
const response = (data) => ({
  ok: true,
  headers: new Headers({ "content-type": "application/json" }),
  json: async () => ({ status: "success", data }),
});
beforeEach(() => vi.clearAllMocks());
it("keeps the memory envelope and pagination request backward compatible", async () => {
  apiFetch.mockResolvedValue(
    response({ facts: [], relations: [], summary: null }),
  );
  expect(await listMemory("c")).toEqual({
    facts: [],
    relations: [],
    summary: null,
    pagination: null,
  });
  await listMemory("c", { page: 5, pageSize: 50 });
  expect(apiFetch.mock.calls.at(-1)[0]).toContain("?page=5&page_size=50");
});
it("sends optimistic preconditions on fact and summary correction", async () => {
  apiFetch
    .mockResolvedValueOnce(
      response({ memory_fact_id: "f", fact_text: "corrected" }),
    )
    .mockResolvedValueOnce(
      response({ summary_id: "s", summary_text: "corrected" }),
    );
  await updateMemoryFact("f", "corrected", { expected_updated_at: "v1" });
  expect(apiFetch.mock.calls[0][1].body).toEqual({
    fact_text: "corrected",
    expected_updated_at: "v1",
  });
  await updateMemorySummary("c", "corrected", {
    expected_updated_at: "v1",
    expected_summary_id: "s",
  });
  expect(apiFetch.mock.calls[1][1].body).toEqual({
    summary_text: "corrected",
    expected_updated_at: "v1",
    expected_summary_id: "s",
  });
});
it("serializes text-only reminder PATCH without date/time fields", async () => {
  apiFetch.mockResolvedValue(
    response({
      reminder_id: "r",
      reminder_text: "fixed",
      due_at: "2026-09-01T10:00:00Z",
      due_at_local: "2026-09-01 13:00",
    }),
  );
  await updateReminder("r", { reminderText: "fixed" });
  expect(apiFetch.mock.calls[0][1].body).toEqual({ reminder_text: "fixed" });
});
it("accepts legacy null/empty lists and rejects malformed list records", async () => {
  apiFetch
    .mockResolvedValueOnce(response(null))
    .mockResolvedValueOnce(response(null))
    .mockResolvedValueOnce(response([null]))
    .mockResolvedValueOnce(response({ facts: [null] }));
  expect(await listReminders()).toEqual([]);
  expect(await listMemory("c")).toMatchObject({
    facts: [],
    relations: [],
    summary: null,
  });
  await expect(listReminders()).rejects.toThrow("Invalid response");
  await expect(listMemory("c")).rejects.toThrow("Invalid response");
});
it("does not silently erase a summary when a successful mutation contains null", async () => {
  apiFetch.mockResolvedValue(response(null));
  await expect(updateMemorySummary("c", "keep this draft")).rejects.toThrow(
    "Invalid response",
  );
});
