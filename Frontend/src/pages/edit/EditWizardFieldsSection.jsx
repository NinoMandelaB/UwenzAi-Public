// src/pages/edit/EditWizardFieldsSection.jsx

import { useState, useEffect, useRef } from "react";
import { useLanguage } from "../../context/LanguageContext";
import { TextInput } from "../character/CharacterFormFields";
import { ChevronDown, Search, X } from "lucide-react";
import {
  GENDER_OPTIONS,
  VISUAL_STYLES,
  SKIN_COLORS,
  HAIR_COLORS,
  BODY_TYPES,
  BREAST_SIZES,
  BUTT_SIZES,
  CHEST_HAIR_OPTIONS,
  MUSCLE_DEFINITIONS,
  BEARD_STYLES,
  RELATIONSHIP_STYLES,
  CLOTHING_STYLES,
} from "./constants";

// ---------------------------------------------------------------------------
// New constants for hair length and updated hair styles
// ---------------------------------------------------------------------------
const HAIR_LENGTHS = [
  { value: "bald", labelKey: "wizard.hairLengthBald" },
  { value: "short", labelKey: "wizard.hairLengthShort" },
  { value: "medium", labelKey: "wizard.hairLengthMedium" },
  { value: "long", labelKey: "wizard.hairLengthLong" },
];

const HAIR_STYLES = [
  { value: "straight", labelKey: "wizard.hairStyleStraight" },
  { value: "wavy", labelKey: "wizard.hairStyleWavy" },
  { value: "curly", labelKey: "wizard.hairStyleCurly" },
  { value: "coily", labelKey: "wizard.hairStyleCurly" },
  { value: "braids", labelKey: "wizard.hairStyleBraids" },
  { value: "dreadlocks", labelKey: "wizard.hairStyleDreadlocks" },
  { value: "afro", labelKey: "wizard.hairStyleAfro" },
  { value: "ponytail", labelKey: "wizard.hairStylePonytail" },
  { value: "buzz_cut", labelKey: "wizard.hairStyleBuzzCut" },
  { value: "other", label: "Other" },
];

const EYE_COLORS = [
  { value: "brown", labelKey: "wizard.eyeBrown" },
  { value: "blue", labelKey: "wizard.eyeBlue" },
  { value: "green", labelKey: "wizard.eyeGreen" },
  { value: "hazel", labelKey: "wizard.eyeHazel" },
  { value: "gray", labelKey: "wizard.eyeGray" },
  { value: "amber", labelKey: "wizard.eyeAmber" },
  { value: "black", labelKey: "wizard.eyeBlack" },
];

const MAX_HOBBIES = 3;

// ---------------------------------------------------------------------------
// Reusable pill selector
// ---------------------------------------------------------------------------
function PillSelector({ label, options, value, onChange, t }) {
  return (
    <div>
      <label className="mb-2 block text-sm font-medium theme-text">
        {label}
      </label>
      <div className="flex flex-wrap gap-2">
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              value === opt.value
                ? "bg-[var(--color-accent)] text-[#082f49]"
                : "bg-[var(--color-soft)] text-[var(--color-text-muted)] border border-[var(--color-border)]"
            }`}
          >
            {opt.emoji ? `${opt.emoji} ` : ""}
            {opt.labelKey ? t(opt.labelKey) : opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Country searchable select
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function EditWizardFieldsSection({
  form,
  updateField,
  occupations,
  hobbiesList,
}) {
  const { t } = useLanguage();
  const gender = form.gender;

  const [selectedOccupation, setSelectedOccupation] = useState(
    form.occupation_id || "",
  );

  useEffect(() => {
    setSelectedOccupation(form.occupation_id || "");
  }, [form.occupation_id]);

  const selectOccupation = (id) => {
    setSelectedOccupation(id);
    updateField("occupation_id", id);
  };

  const hobbyIds = form.hobby_ids || [];

  const toggleHobby = (id) => {
    let nextIds;
    if (hobbyIds.includes(id)) {
      nextIds = hobbyIds.filter((hid) => hid !== id);
    } else {
      if (hobbyIds.length >= MAX_HOBBIES) return;
      nextIds = [...hobbyIds, id];
    }
    updateField("hobby_ids", nextIds);
  };

  return (
    <div className="space-y-5">
      {/* =========================================== */}
      {/* LOCATION CARD                                */}
      {/* =========================================== */}
      <div className="rounded-2xl border p-4 theme-card">
        <h3 className="text-base font-semibold theme-text mb-4">
          {t("characters.location")}
        </h3>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div>
            <label className="mb-2 block text-sm font-medium theme-text">
              {t("wizard.country")}
            </label>
            <SearchableSelect
              value={form.country}
              onChange={(v) => updateField("country", v)}
              placeholder={t("wizard.selectCountry")}
              t={t}
            />
          </div>

          <div>
            <label className="mb-2 block text-sm font-medium theme-text">
              {t("wizard.town")}
            </label>
            <TextInput
              label=""
              value={form.town}
              onChange={(e) => updateField("town", e.target.value)}
              placeholder={t("wizard.townPlaceholder")}
            />
          </div>
        </div>
      </div>

      {/* =========================================== */}
      {/* APPEARANCE & LIFESTYLE CARD                  */}
      {/* =========================================== */}
      <div className="rounded-2xl border p-4 theme-card">
        <h3 className="text-base font-semibold theme-text mb-4">
          {t("characters.appearanceAndLifestyle")}
        </h3>

        {/* Public / Private Toggle */}
        <div className="flex items-center justify-between py-2">
          <div>
            <p className="text-sm font-medium theme-text">
              {t("wizard.publicCharacter")}
            </p>
            <p className="text-xs theme-text-muted">
              {t("wizard.publicCharacterDesc")}
            </p>
          </div>
          <button
            type="button"
            onClick={() => updateField("is_public", !form.is_public)}
            className="relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition"
            style={{
              backgroundColor: form.is_public
                ? "var(--color-accent)"
                : "var(--color-border)",
            }}
          >
            <span
              className="inline-block h-5 w-5 transform rounded-full bg-white transition"
              style={{ marginLeft: form.is_public ? "1.45rem" : "0.2rem" }}
            />
          </button>
        </div>

        {/* --- Gender & Art Style --- */}
        <div className="mt-6">
          <PillSelector
            label={t("wizard.gender")}
            options={GENDER_OPTIONS}
            value={form.gender}
            onChange={(v) => updateField("gender", v)}
            t={t}
          />
        </div>
        <div className="mt-4">
          <PillSelector
            label={t("wizard.visualStyle")}
            options={VISUAL_STYLES}
            value={form.visual_style}
            onChange={(v) => updateField("visual_style", v)}
            t={t}
          />
        </div>

        {/* Age slider */}
        <div className="mt-6">
          <label className="mb-2 block text-sm font-medium theme-text">
            {t("wizard.age")}: {form.age}
          </label>
          <input
            type="range"
            min="18"
            max="100"
            value={form.age}
            onChange={(e) => updateField("age", parseInt(e.target.value, 10))}
            className="w-full accent-[var(--color-accent)]"
          />
        </div>

        {/* Skin & Eyes */}
        <div className="mt-6 border-t border-[var(--color-border)] pt-5">
          <PillSelector
            label={t("wizard.skinColor")}
            options={SKIN_COLORS}
            value={form.skin_color}
            onChange={(v) => updateField("skin_color", v)}
            t={t}
          />
          <div className="mt-4">
            <PillSelector
              label={t("wizard.eyeColor")}
              options={EYE_COLORS}
              value={form.eye_color}
              onChange={(v) => updateField("eye_color", v)}
              t={t}
            />
          </div>
        </div>

        {/* Hair */}
        <div className="mt-6 border-t border-[var(--color-border)] pt-5">
          <PillSelector
            label={t("wizard.hairLength")}
            options={HAIR_LENGTHS}
            value={form.hair_length}
            onChange={(v) => updateField("hair_length", v)}
            t={t}
          />
          <div className="mt-4">
            <PillSelector
              label={t("wizard.hairstyle")}
              options={HAIR_STYLES}
              value={form.hairstyle}
              onChange={(v) => updateField("hairstyle", v)}
              t={t}
            />
          </div>
          <div className="mt-4">
            <PillSelector
              label={t("wizard.hairColor")}
              options={HAIR_COLORS}
              value={form.hair_color}
              onChange={(v) => updateField("hair_color", v)}
              t={t}
            />
          </div>
        </div>

        {/* Body Type */}
        <div className="mt-6">
          <PillSelector
            label={t("wizard.bodyType")}
            options={BODY_TYPES}
            value={form.body_type}
            onChange={(v) => updateField("body_type", v)}
            t={t}
          />
        </div>

        {/* Gender‑specific fields */}
        {gender === "female" && (
          <div className="mt-6 border-t border-[var(--color-border)] pt-5">
            <PillSelector
              label={t("wizard.breastSize")}
              options={BREAST_SIZES}
              value={form.breast_size}
              onChange={(v) => updateField("breast_size", v)}
              t={t}
            />
            <div className="mt-4">
              <PillSelector
                label={t("wizard.buttSize")}
                options={BUTT_SIZES}
                value={form.butt_size}
                onChange={(v) => updateField("butt_size", v)}
                t={t}
              />
            </div>
          </div>
        )}
        {gender === "male" && (
          <div className="mt-6 border-t border-[var(--color-border)] pt-5">
            <PillSelector
              label={t("wizard.chestHair")}
              options={CHEST_HAIR_OPTIONS}
              value={form.chest_hair}
              onChange={(v) => updateField("chest_hair", v)}
              t={t}
            />
            <div className="mt-4">
              <PillSelector
                label={t("wizard.muscleDefinition")}
                options={MUSCLE_DEFINITIONS}
                value={form.muscle_definition}
                onChange={(v) => updateField("muscle_definition", v)}
                t={t}
              />
            </div>
            <div className="mt-4">
              <PillSelector
                label={t("wizard.beardStyle")}
                options={BEARD_STYLES}
                value={form.beard_style}
                onChange={(v) => updateField("beard_style", v)}
                t={t}
              />
            </div>
          </div>
        )}

        {/* Relationship & Clothing */}
        <div className="mt-6 border-t border-[var(--color-border)] pt-5">
          <PillSelector
            label={t("wizard.relationshipStyle")}
            options={RELATIONSHIP_STYLES}
            value={form.relationship_style}
            onChange={(v) => updateField("relationship_style", v)}
            t={t}
          />
          <div className="mt-4">
            <PillSelector
              label={t("wizard.clothingStyle")}
              options={CLOTHING_STYLES}
              value={form.clothing_style}
              onChange={(v) => updateField("clothing_style", v)}
              t={t}
            />
          </div>
        </div>

        {/* Occupation */}
        <div className="mt-6 border-t border-[var(--color-border)] pt-5">
          <label className="mb-2 block text-sm font-medium theme-text">
            {t("wizard.occupation")}
          </label>
          {occupations.length === 0 ? (
            <p className="text-sm theme-text-muted">
              {t("characters.noOccupationsAvailable")}
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {occupations.map((occ) => {
                const isSelected = selectedOccupation === occ.id;
                return (
                  <button
                    key={occ.id}
                    type="button"
                    onClick={() => selectOccupation(occ.id)}
                    className="flex items-start gap-3 rounded-xl border p-4 text-left transition hover:border-cyan-400/50"
                    style={{
                      borderColor: isSelected
                        ? "var(--color-accent)"
                        : "var(--color-border)",
                      backgroundColor: isSelected
                        ? "var(--color-accent-soft)"
                        : "var(--color-card)",
                    }}
                  >
                    <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent-soft)] text-sm font-bold text-[var(--color-accent)]">
                      {occ.name.charAt(0).toUpperCase()}
                    </span>
                    <div className="flex-1">
                      <div className="flex items-center justify-between">
                        <span
                          className="font-medium"
                          style={{
                            color: isSelected
                              ? "var(--color-accent)"
                              : "var(--color-text)",
                          }}
                        >
                          {occ.name}
                        </span>
                        {isSelected && (
                          <span
                            className="h-5 w-5 rounded-full"
                            style={{ backgroundColor: "var(--color-accent)" }}
                          >
                            <svg
                              className="h-5 w-5 text-white"
                              viewBox="0 0 20 20"
                              fill="currentColor"
                            >
                              <path
                                fillRule="evenodd"
                                d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                                clipRule="evenodd"
                              />
                            </svg>
                          </span>
                        )}
                      </div>
                      {occ.description && (
                        <p className="mt-1 text-xs theme-text-muted">
                          {occ.description}
                        </p>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Hobbies */}
        <div className="mt-6 border-t border-[var(--color-border)] pt-5">
          <div className="flex items-center justify-between mb-2">
            <label className="text-sm font-medium theme-text">
              {t("wizard.hobbies")} ({t("characters.upTo")} {MAX_HOBBIES})
            </label>
            <span
              className="rounded-full px-3 py-1 text-xs font-medium"
              style={{
                backgroundColor: "var(--color-soft)",
                color: "var(--color-text-muted)",
              }}
            >
              {hobbyIds.length} / {MAX_HOBBIES}
            </span>
          </div>

          {hobbiesList.length === 0 ? (
            <p className="text-sm theme-text-muted">
              {t("characters.noHobbiesAvailable")}
            </p>
          ) : (
            <>
              {hobbyIds.length >= MAX_HOBBIES && (
                <div className="mb-3 rounded-xl border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-300">
                  {t("characters.maxHobbiesReached", { max: MAX_HOBBIES })}
                </div>
              )}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {hobbiesList.map((hobby) => {
                  const isSelected = hobbyIds.includes(hobby.id);
                  const isDisabled =
                    !isSelected && hobbyIds.length >= MAX_HOBBIES;

                  return (
                    <button
                      key={hobby.id}
                      type="button"
                      onClick={() => toggleHobby(hobby.id)}
                      disabled={isDisabled}
                      className={`flex items-start gap-3 rounded-xl border p-4 text-left transition hover:border-cyan-400/50 ${
                        isDisabled ? "opacity-50 cursor-not-allowed" : ""
                      }`}
                      style={{
                        borderColor: isSelected
                          ? "var(--color-accent)"
                          : "var(--color-border)",
                        backgroundColor: isSelected
                          ? "var(--color-accent-soft)"
                          : "var(--color-card)",
                      }}
                    >
                      <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent-soft)] text-sm font-bold text-[var(--color-accent)]">
                        {hobby.name.charAt(0).toUpperCase()}
                      </span>
                      <div className="flex-1">
                        <div className="flex items-center justify-between">
                          <span
                            className="font-medium"
                            style={{
                              color: isSelected
                                ? "var(--color-accent)"
                                : "var(--color-text)",
                            }}
                          >
                            {hobby.name}
                          </span>
                          {isSelected && (
                            <span
                              className="h-5 w-5 rounded-full"
                              style={{ backgroundColor: "var(--color-accent)" }}
                            >
                              <svg
                                className="h-5 w-5 text-white"
                                viewBox="0 0 20 20"
                                fill="currentColor"
                              >
                                <path
                                  fillRule="evenodd"
                                  d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                                  clipRule="evenodd"
                                />
                              </svg>
                            </span>
                          )}
                        </div>
                        {hobby.description && (
                          <p className="mt-1 text-xs theme-text-muted">
                            {hobby.description}
                          </p>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
