import { fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import CreditsModal from "./CreditsModal";
import { CREDITS } from "../config/credits";

// Stable identity (like the real LanguageContext, which memoizes t).
const stableT = (path) => path;

vi.mock("../context/LanguageContext", () => ({
  useLanguage: () => ({ language: "en", t: stableT }),
}));

describe("CreditsModal", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <CreditsModal isOpen={false} onClose={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("lists every contributor from config/credits.js", () => {
    render(<CreditsModal isOpen onClose={() => {}} />);

    for (const contributor of CREDITS.contributors) {
      expect(screen.getByText(contributor.name)).toBeTruthy();
      expect(screen.getByText(contributor.role.en)).toBeTruthy();
    }
  });

  it("renders a labeled link for every contributor URL", () => {
    render(<CreditsModal isOpen onClose={() => {}} />);

    const links = screen.getAllByRole("link");
    const hrefs = links.map((link) => link.getAttribute("href"));

    for (const contributor of CREDITS.contributors) {
      const urls =
        contributor.urls ?? (contributor.url ? [contributor.url] : []);
      for (const entry of urls) {
        const url = typeof entry === "string" ? entry : entry.url;
        expect(hrefs).toContain(url);
      }
    }

    // Multiple links per contributor render as pills with friendly labels
    expect(screen.getByText("GitHub")).toBeTruthy();
    expect(screen.getByText("Email")).toBeTruthy();
    expect(screen.getAllByText("LinkedIn").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Fiverr").length).toBeGreaterThan(0);
  });

  it("shows shipped items grouped under one label per week", () => {
    render(<CreditsModal isOpen onClose={() => {}} />);

    expect(screen.getByText("credits.shippedTitle")).toBeTruthy();

    for (const item of CREDITS.shipped) {
      expect(screen.getByText(item.en)).toBeTruthy();
    }

    // Same-week items share a single label — one per distinct week
    const weekOf = (dateStr) => {
      const date = new Date(`${dateStr}T00:00:00`);
      const monday = new Date(date);
      monday.setDate(
        date.getDate() - (date.getDay() === 0 ? 6 : date.getDay() - 1),
      );
      return monday.toISOString().slice(0, 10);
    };
    const distinctWeeks = new Set(
      CREDITS.shipped.map((item) => weekOf(item.date)),
    );

    const weekLabels = screen.getAllByText(/^Week of /);
    expect(weekLabels.length).toBe(distinctWeeks.size);
    expect(weekLabels.length).toBeLessThan(CREDITS.shipped.length);
  });

  it("shows the roadmap section", () => {
    render(<CreditsModal isOpen onClose={() => {}} />);

    expect(screen.getByText("credits.nextTitle")).toBeTruthy();

    for (const item of CREDITS.roadmap) {
      expect(screen.getByText(item.en)).toBeTruthy();
    }
  });

  it("links to the public repository when configured", () => {
    render(<CreditsModal isOpen onClose={() => {}} />);

    if (CREDITS.openSourceUrl) {
      const link = screen.getByRole("link", { name: /openSource/i });
      expect(link.getAttribute("href")).toBe(CREDITS.openSourceUrl);
    }
  });

  it("closes via the close button and backdrop click", () => {
    const onClose = vi.fn();
    render(<CreditsModal isOpen onClose={onClose} />);

    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);

    // Backdrop: click the overlay around the dialog, not the dialog itself
    fireEvent.click(screen.getByRole("dialog").parentElement);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
