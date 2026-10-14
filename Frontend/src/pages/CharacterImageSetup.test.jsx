// src/pages/CharacterImageSetup.test.jsx
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CharacterImageSetup from "./CharacterImageSetup";
import { translations } from "../i18n/translations";

const t = (key, vars = {}) => {
  let value = key
    .split(".")
    .reduce((acc, part) => acc?.[part], translations.en);
  if (typeof value !== "string") return key;
  Object.entries(vars).forEach(([k, v]) => {
    value = value.replace(`{${k}}`, v);
  });
  return value;
};
vi.mock("../context/LanguageContext", () => ({
  useLanguage: () => ({ t, language: "en" }),
}));

vi.mock("../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: true }),
}));

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({ user: { user_id: "u1" }, isAdmin: false }),
}));

const getCharacterById = vi.fn();
const generateAvatar = vi.fn();
const listGalleryImages = vi.fn();
const generateGalleryImage = vi.fn();
const uploadGalleryImage = vi.fn();
const deleteGalleryImage = vi.fn();
const updateCharacter = vi.fn();
const completeCharacterSetup = vi.fn();

vi.mock("../services/characterService", () => ({
  getCharacterById: (...args) => getCharacterById(...args),
  generateAvatar: (...args) => generateAvatar(...args),
  listGalleryImages: (...args) => listGalleryImages(...args),
  generateGalleryImage: (...args) => generateGalleryImage(...args),
  uploadGalleryImage: (...args) => uploadGalleryImage(...args),
  deleteGalleryImage: (...args) => deleteGalleryImage(...args),
  updateCharacter: (...args) => updateCharacter(...args),
  completeCharacterSetup: (...args) => completeCharacterSetup(...args),
}));

vi.mock("../services/billingService", () => ({
  getPricing: vi.fn().mockResolvedValue({
    data: { image_generation_cost: 350 },
  }),
}));

function draftCharacter(overrides = {}) {
  return {
    data: {
      character: {
        character_id: "char-1",
        name: "Amina",
        visual_style: "realistic",
        avatar_url: null,
        // The serializer emits the lowercase model value ("draft"), not "DRAFT".
        review_status: "draft",
        avatar_source: "ai_generated",
        ...overrides,
      },
    },
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/characters/char-1/setup"]}>
      <Routes>
        <Route
          path="/characters/:characterId/setup"
          element={<CharacterImageSetup />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listGalleryImages.mockResolvedValue({ data: { images: [] } });
});

afterEach(cleanup);

describe("CharacterImageSetup automatic avatar generation", () => {
  it("generates the first avatar automatically for a DRAFT character without one", async () => {
    getCharacterById.mockResolvedValue(draftCharacter());
    generateAvatar.mockResolvedValue({ data: { prompt_used: "x" } });

    renderPage();

    await waitFor(() => {
      expect(generateAvatar).toHaveBeenCalledWith(
        "char-1",
        expect.objectContaining({ extra_prompt: "" }),
      );
    });
    expect(generateAvatar).toHaveBeenCalledTimes(1);
  });

  it("does not auto-generate when the character already has an avatar", async () => {
    getCharacterById.mockResolvedValue(
      draftCharacter({ avatar_url: "https://example.com/a.webp" }),
    );

    renderPage();

    // Wait for the page to settle, then assert no generation was requested.
    await waitFor(() => {
      expect(screen.getByText("Regenerate Avatar Image")).toBeTruthy();
    });
    expect(generateAvatar).not.toHaveBeenCalled();
  });

  it("does not auto-generate for a character outside DRAFT review status", async () => {
    getCharacterById.mockResolvedValue(
      draftCharacter({ review_status: "pending" }),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Generate Avatar Image")).toBeTruthy();
    });
    expect(generateAvatar).not.toHaveBeenCalled();
  });

  it("shows the regenerate label only once an avatar exists", async () => {
    getCharacterById.mockResolvedValue(
      draftCharacter({ avatar_url: "https://example.com/a.webp" }),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Regenerate Avatar Image")).toBeTruthy();
    });
  });

  it("shows the auto-generation failure message when the automatic attempt fails", async () => {
    getCharacterById.mockResolvedValue(draftCharacter());
    // An error with no usable message falls back to the auto-failure text.
    generateAvatar.mockRejectedValue(new Error(""));

    renderPage();

    await waitFor(() => {
      expect(
        screen.getByText(
          "We couldn't create the avatar automatically. Please try again.",
        ),
      ).toBeTruthy();
    });
    // The manual button remains available for a retry.
    expect(screen.getByText("Generate Avatar Image")).toBeTruthy();
  });
});

describe("CharacterImageSetup gallery and draft handling", () => {
  const withAvatar = (overrides = {}) =>
    draftCharacter({
      avatar_url: "https://example.com/a.webp",
      is_public: true,
      ...overrides,
    });

  it("marks generated gallery pictures as part of the character's public gallery", async () => {
    getCharacterById.mockResolvedValue(withAvatar());
    generateGalleryImage.mockResolvedValue({ data: {} });

    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Regenerate Avatar Image")).toBeTruthy();
    });

    // The gallery card's "Generate" opens the extra-details dialog, whose own
    // "Generate" button starts the request.
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    const dialog = (
      await screen.findByText("Add extra details (optional)")
    ).closest("div");
    fireEvent.click(within(dialog).getByRole("button", { name: "Generate" }));

    await waitFor(() => {
      expect(generateGalleryImage).toHaveBeenCalledWith(
        "char-1",
        expect.objectContaining({ publish_on_approval: true }),
      );
    });
  });

  it("labels pictures that wait for the character's approval", async () => {
    getCharacterById.mockResolvedValue(withAvatar());
    listGalleryImages.mockResolvedValue({
      data: {
        images: [
          {
            id: "i1",
            image_url: "https://example.com/g1.webp",
            publish_on_approval: true,
          },
          {
            id: "i2",
            image_url: "https://example.com/g2.webp",
            publish_on_approval: false,
          },
        ],
      },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText("Public after approval")).toHaveLength(1);
    });
  });

  it("explains that a public draft is not reviewed until setup is completed", async () => {
    getCharacterById.mockResolvedValue(withAvatar());

    renderPage();

    await waitFor(() => {
      expect(
        screen.getByText(/stays a draft that only you can see/),
      ).toBeTruthy();
    });
    expect(screen.getByText("Save as draft")).toBeTruthy();
  });

  it("does not show the draft hint for a private character", async () => {
    getCharacterById.mockResolvedValue(withAvatar({ is_public: false }));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Regenerate Avatar Image")).toBeTruthy();
    });
    expect(
      screen.queryByText(/stays a draft that only you can see/),
    ).toBeNull();
  });
});
