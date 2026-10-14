// src/components/Billing/SparkPackageCard.test.jsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import SparkPackageCard from "./SparkPackageCard";

vi.mock("../../context/LanguageContext", () => ({
  useLanguage: () => ({
    t: (path, vars) => {
      const strings = {
        "spark_pool.sparks": "Sparks",
        "spark_pool.packageUsageText": "Use sparks for chats and photos.",
        "spark_pool.vatIncluded": "incl. {vat}% VAT",
      };
      let out = strings[path] ?? path;
      if (vars) {
        Object.entries(vars).forEach(([k, v]) => {
          out = out.replace(`{${k}}`, v);
        });
      }
      return out;
    },
  }),
}));

const pkg = {
  slug: "starter",
  name: "Starter",
  description: "300 sparks",
  base_spark_amount: 300,
  base_price_kes: "30.00",
  discount_percent: "30.00",
  vat_percent: "16.00",
  final_price_kes: "24.36",
  final_price_usd: "0.19",
  final_price_eur: "0.16",
};

describe("SparkPackageCard", () => {
  it("shows the VAT-inclusive price with the VAT rate", () => {
    render(<SparkPackageCard pkg={pkg} onSelect={() => {}} />);

    // getByText throws when the element is missing, so this pins presence.
    expect(screen.getByText("KES 24.36")).toBeTruthy();
    expect(screen.getByText("incl. 16% VAT")).toBeTruthy();
  });

  it("shows approximate USD and EUR prices below the KES price", () => {
    render(<SparkPackageCard pkg={pkg} onSelect={() => {}} />);

    expect(screen.getByText(/≈ \$0\.19 · €0\.16/)).toBeTruthy();
  });

  it("hides the converted price line when FX rates are unavailable", () => {
    const { final_price_usd, final_price_eur, ...noFx } = pkg;
    render(<SparkPackageCard pkg={noFx} onSelect={() => {}} />);

    expect(screen.queryByText(/≈/)).toBeNull();
  });

  it("omits the VAT line when the package has no VAT", () => {
    render(
      <SparkPackageCard
        pkg={{ ...pkg, vat_percent: "0.00", final_price_kes: "21.00" }}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("KES 21.00")).toBeTruthy();
    expect(screen.queryByText(/VAT/)).toBeNull();
  });

  it("selects the package on click", () => {
    const onSelect = vi.fn();
    render(<SparkPackageCard pkg={pkg} onSelect={onSelect} />);

    fireEvent.click(screen.getByText("KES 24.36"));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("shows the Google Play price instead of KES inside the app", () => {
    render(
      <SparkPackageCard pkg={pkg} playPrice="KES 120.00" onSelect={() => {}} />,
    );

    expect(screen.getByText("KES 120.00")).toBeTruthy();
    // The website price, VAT rate and FX conversions must not be advertised
    // alongside Play's price inside the Android app.
    expect(screen.queryByText("KES 24.36")).toBeNull();
    expect(screen.queryByText(/≈/)).toBeNull();
    expect(screen.queryByText(/VAT/)).toBeNull();
  });
});
