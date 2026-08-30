import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import GuestModeUpsellText from "./GuestModeUpsellText";

describe("GuestModeUpsellText", () => {
  it("renders the signup bonus in bold and feature lines as a list", () => {
    render(
      <GuestModeUpsellText
        text={
          "Create an account and get **2000 bonus Sparks instantly**.\n- Better character memory\n- BYOK"
        }
      />,
    );

    expect(screen.getByText("2000 bonus Sparks instantly").tagName).toBe(
      "STRONG",
    );
    expect(screen.getByRole("list")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("renders the Kiswahili bonus and feature list", () => {
    render(
      <GuestModeUpsellText
        text={
          "Fungua akaunti upate **Sparks 2000 za ziada mara moja**.\n- Kumbukumbu bora ya mhusika"
        }
      />,
    );

    expect(screen.getByText("Sparks 2000 za ziada mara moja").tagName).toBe(
      "STRONG",
    );
    expect(screen.getByText("Kumbukumbu bora ya mhusika")).toBeTruthy();
  });
});
