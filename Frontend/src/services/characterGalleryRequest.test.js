import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("./apiClient", () => ({
  apiFetch: (...args) => apiFetch(...args),
}));

import { generateGalleryImage } from "./characterService";

beforeEach(() => {
  vi.clearAllMocks();
  apiFetch.mockImplementation(
    async () =>
      new Response(JSON.stringify({ status: "success", data: {} }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
  );
});

describe("generateGalleryImage", () => {
  it("sends publish_on_approval only when the caller asks for it", async () => {
    await generateGalleryImage("c1", { publish_on_approval: true });
    expect(apiFetch.mock.calls[0][1].body.publish_on_approval).toBe(true);
  });

  it("leaves it out for detail-page and chat style requests", async () => {
    await generateGalleryImage("c1", { extra_prompt: "at the beach" });
    expect(apiFetch.mock.calls[0][1].body).not.toHaveProperty(
      "publish_on_approval",
    );
  });
});
