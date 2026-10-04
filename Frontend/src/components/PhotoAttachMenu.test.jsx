import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import PhotoAttachMenu from "./PhotoAttachMenu";

// The component's only external dependency is the language context; a
// minimal spy keeps the test focused on structure and behaviour.
vi.mock("../context/LanguageContext", () => ({
  useLanguage: () => ({
    t: (path) =>
      ({
        "chat.attachPhoto": "Send a photo",
        "chat.choosePhoto": "Choose photo",
        "chat.takePhoto": "Take photo",
      })[path] || path,
  }),
}));

describe("PhotoAttachMenu", () => {
  it("renders the trigger button and no menu initially", () => {
    render(<PhotoAttachMenu onFileSelected={() => {}} />);
    expect(screen.getByRole("button", { name: "Send a photo" })).toBeTruthy();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("opens a menu with both photo sources", () => {
    render(<PhotoAttachMenu onFileSelected={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Send a photo" }));
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Choose photo" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Take photo" })).toBeTruthy();
  });

  it("wires the camera source to a capture input and the gallery to a plain input", () => {
    const { container } = render(<PhotoAttachMenu onFileSelected={() => {}} />);
    const inputs = container.querySelectorAll('input[type="file"]');
    expect(inputs).toHaveLength(2);
    const [gallery, camera] = inputs;
    expect(gallery.hasAttribute("capture")).toBe(false);
    expect(camera.getAttribute("capture")).toBe("environment");
    // Both accept the same server-side allowlist of photo types.
    for (const input of inputs) {
      expect(input.getAttribute("accept")).toBe(
        "image/jpeg,image/png,image/webp",
      );
    }
  });

  it("closes the menu after choosing a source", () => {
    render(<PhotoAttachMenu onFileSelected={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Send a photo" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Take photo" }));
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes on Escape", () => {
    render(<PhotoAttachMenu onFileSelected={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Send a photo" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("disables the trigger when the composer is busy", () => {
    render(<PhotoAttachMenu disabled onFileSelected={() => {}} />);
    expect(screen.getByRole("button", { name: "Send a photo" }).disabled).toBe(
      true,
    );
  });
});
