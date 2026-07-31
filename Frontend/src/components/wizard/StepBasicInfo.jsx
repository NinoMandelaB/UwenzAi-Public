// src/components/character/wizard/StepBasicInfo.jsx

import { useState, useRef, useEffect } from "react";
import { useWizard } from "../../context/WizardContext";
import { useLanguage } from "../../context/LanguageContext";
import PageCard from "../PageCard";
import { ChevronDown, Search, X } from "lucide-react";

// Images
import realistic_female from "../../assets/images/wizard/female/artstyle/realistic_female.jpg";
import anime_female from "../../assets/images/wizard/female/artstyle/anime_female.jpg";
import cartoon_female from "../../assets/images/wizard/female/artstyle/cartoon_female.jpg";
import realistic_male from "../../assets/images/wizard/male/artstyle/realistic_male.jpg";
import anime_male from "../../assets/images/wizard/male/artstyle/anime_male.jpg";
import cartoon_male from "../../assets/images/wizard/male/artstyle/cartoon_male.jpg";

const IMAGE_MAP = {
  female: {
    realistic: realistic_female,
    anime: anime_female,
    cartoon: cartoon_female,
  },
  male: {
    realistic: realistic_male,
    anime: anime_male,
    cartoon: cartoon_male,
  },
};

const GENDER_OPTIONS = [
  { value: "female", labelKey: "wizard.genderFemale" },
  { value: "male", labelKey: "wizard.genderMale" },
];

const STYLE_OPTIONS = [
  { value: "realistic", labelKey: "wizard.styleRealistic" },
  { value: "anime", labelKey: "wizard.styleAnime" },
  { value: "cartoon", labelKey: "wizard.styleCartoon" },
];

// Countries
const ALL_COUNTRIES = [
  "Afghanistan",
  "Albania",
  "Algeria",
  "Andorra",
  "Angola",
  "Argentina",
  "Armenia",
  "Australia",
  "Austria",
  "Azerbaijan",
  "Bahamas",
  "Bahrain",
  "Bangladesh",
  "Belgium",
  "Benin",
  "Brazil",
  "Canada",
  "China",
  "Colombia",
  "Denmark",
  "Egypt",
  "Ethiopia",
  "Finland",
  "France",
  "Germany",
  "Ghana",
  "Greece",
  "India",
  "Indonesia",
  "Iran",
  "Iraq",
  "Ireland",
  "Israel",
  "Italy",
  "Japan",
  "Kenya",
  "Malaysia",
  "Mexico",
  "Netherlands",
  "Nigeria",
  "Norway",
  "Pakistan",
  "Poland",
  "Portugal",
  "Qatar",
  "Russia",
  "Rwanda",
  "Saudi Arabia",
  "Singapore",
  "South Africa",
  "South Korea",
  "Spain",
  "Sweden",
  "Switzerland",
  "Tanzania",
  "Thailand",
  "Turkey",
  "Uganda",
  "Ukraine",
  "United Arab Emirates",
  "United Kingdom",
  "United States",
  "Vietnam",
  "Zambia",
  "Zimbabwe",
];

const TOP_COUNTRIES = ["Kenya", "Tanzania", "Uganda", "Rwanda"];

const otherCountries = ALL_COUNTRIES.filter(
  (c) => !TOP_COUNTRIES.includes(c),
).sort((a, b) => a.localeCompare(b));

const ALL_COUNTRIES_WITH_PINNED = [
  ...TOP_COUNTRIES.filter((c) => ALL_COUNTRIES.includes(c)),
  ...otherCountries,
];

// ---------------- SEARCHABLE SELECT ----------------

function SearchableSelect({ value, onChange, placeholder, t }) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [dropdownDirection, setDropdownDirection] = useState("down");
  const containerRef = useRef(null);

  const filteredCountries = ALL_COUNTRIES_WITH_PINNED.filter((country) =>
    country.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  const showGroups = searchTerm.length === 0;

  const pinnedFiltered = showGroups
    ? filteredCountries.filter((c) => TOP_COUNTRIES.includes(c))
    : [];

  const otherFiltered = showGroups
    ? filteredCountries.filter((c) => !TOP_COUNTRIES.includes(c))
    : filteredCountries;

  const handleSelect = (country) => {
    onChange(country);
    setIsOpen(false);
    setSearchTerm("");
  };

  // Flip logic
  useEffect(() => {
    if (isOpen && containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;

      setDropdownDirection(
        spaceBelow < 260 && spaceAbove > spaceBelow ? "up" : "down",
      );
    }
  }, [isOpen]);

  // Outside click
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setIsOpen(false);
        setSearchTerm("");
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative" ref={containerRef}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setIsOpen((p) => !p)}
        className="flex h-[50px] w-full items-center justify-between rounded-xl border px-4 text-sm transition"
        style={{
          borderColor: isOpen ? "var(--color-accent)" : "var(--color-border)",
          backgroundColor: "var(--color-card)",
          color: "var(--color-text)",
        }}
      >
        <span className={!value ? "opacity-60" : ""}>
          {value || placeholder || t("wizard.selectCountry")}
        </span>

        <ChevronDown
          size={18}
          className={`transition-transform duration-200 ${
            isOpen ? "rotate-180" : ""
          }`}
        />
      </button>

      {/* Dropdown */}
      {isOpen && (
        <div
          className={`absolute z-50 w-full overflow-hidden rounded-2xl border shadow-2xl ${
            dropdownDirection === "up" ? "bottom-full mb-2" : "top-full mt-2"
          }`}
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-card-solid, var(--color-card))",
          }}
        >
          {/* Search */}
          <div
            className="border-b p-3"
            style={{ borderColor: "var(--color-border)" }}
          >
            <div
              className="flex h-11 items-center gap-2 rounded-xl border px-3"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-soft)",
              }}
            >
              <Search size={16} style={{ color: "var(--color-text-muted)" }} />

              <input
                autoFocus
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder={t("wizard.searchCountry")}
                className="flex-1 bg-transparent text-sm outline-none"
                style={{ color: "var(--color-text)" }}
              />

              {searchTerm && (
                <button
                  type="button"
                  onClick={() => setSearchTerm("")}
                  className="flex h-6 w-6 items-center justify-center rounded-full"
                  style={{
                    backgroundColor: "var(--color-soft)",
                    color: "var(--color-text-muted)",
                  }}
                >
                  <X size={13} />
                </button>
              )}
            </div>
          </div>

          {/* Options */}
          <div className="max-h-[260px] overflow-y-auto py-1">
            {filteredCountries.length === 0 ? (
              <div
                className="px-4 py-4 text-center text-sm"
                style={{ color: "var(--color-text-muted)" }}
              >
                {t("wizard.noCountriesFound")}
              </div>
            ) : (
              <>
                {/* Pinned */}
                {showGroups && pinnedFiltered.length > 0 && (
                  <>
                    <div
                      className="px-4 py-2 text-xs font-semibold uppercase tracking-wide"
                      style={{ color: "var(--color-text-muted)" }}
                    >
                      {t("wizard.popularCountries")}
                    </div>

                    {pinnedFiltered.map((country) => (
                      <button
                        key={country}
                        type="button"
                        onClick={() => handleSelect(country)}
                        className="w-full px-4 py-3 text-left text-sm transition hover:bg-soft md:px-4"
                        style={{
                          backgroundColor:
                            value === country
                              ? "var(--color-accent-soft)"
                              : "transparent",
                          color: "var(--color-text)",
                        }}
                        onMouseEnter={(e) => {
                          if (value !== country)
                            e.currentTarget.style.backgroundColor =
                              "var(--color-soft)";
                        }}
                        onMouseLeave={(e) => {
                          if (value !== country)
                            e.currentTarget.style.backgroundColor =
                              "transparent";
                        }}
                      >
                        {country}
                      </button>
                    ))}

                    <div
                      className="my-1 border-t"
                      style={{ borderColor: "var(--color-border)" }}
                    />
                  </>
                )}

                {/* Others */}
                {(showGroups ? otherFiltered : filteredCountries).map(
                  (country) => (
                    <button
                      key={country}
                      type="button"
                      onClick={() => handleSelect(country)}
                      className="w-full px-4 py-3 text-left text-sm transition hover:bg-soft md:px-4"
                      style={{
                        backgroundColor:
                          value === country
                            ? "var(--color-accent-soft)"
                            : "transparent",
                        color: "var(--color-text)",
                      }}
                      onMouseEnter={(e) => {
                        if (value !== country)
                          e.currentTarget.style.backgroundColor =
                            "var(--color-soft)";
                      }}
                      onMouseLeave={(e) => {
                        if (value !== country)
                          e.currentTarget.style.backgroundColor = "transparent";
                      }}
                    >
                      {country}
                    </button>
                  ),
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------- MAIN COMPONENT ----------------

export default function StepBasicInfo() {
  const { state, updateState } = useWizard();
  const { t } = useLanguage();

  const handleChange = (field, value) => {
    updateState({ [field]: value });
  };

  return (
    <PageCard className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold theme-text">
          {t("wizard.step1.title")}
        </h3>
        <p className="mt-1 text-sm theme-text-muted">
          {t("wizard.step1.subtitle")}
        </p>
      </div>

      <div className="space-y-8">
        {/* Name */}
        <div className="space-y-2">
          <label className="text-sm font-medium theme-text">
            {t("wizard.name")} <span className="text-red-400">*</span>
          </label>

          <input
            type="text"
            value={state.name}
            onChange={(e) => handleChange("name", e.target.value)}
            placeholder={t("wizard.namePlaceholder")}
            className="h-[50px] w-full rounded-xl border px-4 text-sm"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-card)",
              color: "var(--color-text)",
            }}
          />
        </div>

        {/* Gender */}
        <div className="space-y-3">
          <label className="text-sm font-medium theme-text">
            {t("wizard.gender")}
          </label>

          <div className="flex gap-2">
            {GENDER_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => handleChange("gender", opt.value)}
                className="rounded-md px-3 py-1.5 text-xs"
                style={{
                  backgroundColor:
                    state.gender === opt.value
                      ? "var(--color-accent)"
                      : "var(--color-soft)",
                  color:
                    state.gender === opt.value
                      ? "#082f49"
                      : "var(--color-text)",
                }}
              >
                {t(opt.labelKey)}
              </button>
            ))}
          </div>
        </div>

        {/* Style */}
        <div className="grid grid-cols-3 gap-3">
          {STYLE_OPTIONS.map((opt) => {
            const img = IMAGE_MAP[state.gender][opt.value];
            const active = state.visual_style === opt.value;

            return (
              <div key={opt.value} className="space-y-2">
                <div
                  onClick={() => handleChange("visual_style", opt.value)}
                  className="aspect-[3/4] cursor-pointer overflow-hidden rounded-xl border"
                  style={{
                    borderColor: active
                      ? "var(--color-accent)"
                      : "var(--color-border)",
                  }}
                >
                  <img src={img} className="h-full w-full object-cover" />
                </div>

                <button
                  type="button"
                  onClick={() => handleChange("visual_style", opt.value)}
                  className="w-full rounded-md px-3 py-1.5 text-xs"
                  style={{
                    backgroundColor: active
                      ? "var(--color-accent)"
                      : "var(--color-soft)",
                    color: active ? "#082f49" : "var(--color-text)",
                  }}
                >
                  {t(opt.labelKey)}
                </button>
              </div>
            );
          })}
        </div>

        {/* Location - Responsive: stacked on mobile, side by side on larger screens */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <label className="text-sm font-medium theme-text">
              {t("wizard.country")}
            </label>

            <SearchableSelect
              value={state.country || "Kenya"}
              onChange={(v) => handleChange("country", v)}
              placeholder={t("wizard.selectCountry")}
              t={t}
            />
          </div>

          <div>
            <label className="text-sm font-medium theme-text">
              {t("wizard.town")}
            </label>

            <input
              type="text"
              value={state.town}
              onChange={(e) => handleChange("town", e.target.value)}
              placeholder={t("wizard.townPlaceholder")}
              className="h-[50px] w-full rounded-xl border px-4 text-sm"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-card)",
                color: "var(--color-text)",
              }}
            />
          </div>
        </div>

        {/* Visibility */}
        <div className="space-y-2">
          <label className="text-sm font-medium theme-text">
            {t("wizard.visibility")}
          </label>

          <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
            {t("wizard.visibilityDesc")}
          </p>

          <div className="flex gap-2 pt-1">
            {[
              { value: true, labelKey: "wizard.public" },
              { value: false, labelKey: "wizard.private" },
            ].map((opt) => {
              const selected = (state.is_public ?? true) === opt.value;

              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => handleChange("is_public", opt.value)}
                  className="rounded-md px-3 py-1.5 text-xs"
                  style={{
                    backgroundColor: selected
                      ? "var(--color-accent)"
                      : "var(--color-soft)",
                    color: selected ? "#082f49" : "var(--color-text)",
                  }}
                >
                  {t(opt.labelKey)}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </PageCard>
  );
}
