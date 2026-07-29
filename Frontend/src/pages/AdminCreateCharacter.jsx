import { useState } from "react";
import PageCard from "../components/PageCard";
import PrimaryButton from "../components/PrimaryButton";
import SectionTitle from "../components/SectionTitle";

function FieldLabel({ children }) {
  return (
    <label className="mb-2 block text-sm font-medium theme-text-muted">
      {children}
    </label>
  );
}

function TextInput({ placeholder, value, onChange, type = "text" }) {
  return (
    <input
      type={type}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      className="w-full rounded-2xl border px-4 py-3 text-sm outline-none transition focus:opacity-90"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-input)",
        color: "var(--color-text)",
      }}
    />
  );
}

function TextArea({ placeholder, value, onChange, rows = 4 }) {
  return (
    <textarea
      rows={rows}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      className="w-full resize-none rounded-2xl border px-4 py-3 text-sm leading-6 outline-none transition focus:opacity-90"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-input)",
        color: "var(--color-text)",
      }}
    />
  );
}

function SelectInput({ value, onChange, children }) {
  return (
    <select
      value={value}
      onChange={onChange}
      className="w-full rounded-2xl border px-4 py-3 text-sm outline-none transition focus:opacity-90"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-input)",
        color: "var(--color-text)",
      }}
    >
      {children}
    </select>
  );
}

function ToggleItem({ title, subtitle, checked, onChange }) {
  return (
    <div
      className="flex items-center justify-between gap-3 rounded-2xl border p-4"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-soft)",
      }}
    >
      <div className="min-w-0">
        <p className="text-sm font-medium theme-text">{title}</p>
        <p className="mt-1 text-sm theme-text-muted">{subtitle}</p>
      </div>

      <button
        type="button"
        onClick={onChange}
        className={`relative h-7 w-12 rounded-full transition ${
          checked ? "bg-[var(--color-accent)]" : "bg-slate-500/60"
        }`}
      >
        <span
          className={`absolute top-1 h-5 w-5 rounded-full bg-white transition ${
            checked ? "left-6" : "left-1"
          }`}
        />
      </button>
    </div>
  );
}

export default function AdminCreateCharacter() {
  const [form, setForm] = useState({
    name: "",
    tagline: "",
    personality: "",
    greeting: "",
    tone: "Warm",
    language: "English",
    avatarUrl: "",
    status: "Draft",
    nsfwEnabled: false,
  });

  function updateField(field, value) {
    setForm((prev) => ({
      ...prev,
      [field]: value,
    }));
  }

  return (
    <div className="space-y-6">
      <section
        className="rounded-3xl border p-5 shadow-xl"
        style={{
          borderColor:
            "color-mix(in srgb, var(--color-guest) 25%, transparent)",
          backgroundColor:
            "color-mix(in srgb, var(--color-guest) 10%, transparent)",
        }}
      >
        <p className="text-sm" style={{ color: "var(--color-guest)" }}>
          Admin Panel
        </p>

        <h2 className="mt-2 text-2xl font-bold tracking-tight theme-text">
          Create Character
        </h2>

        <p className="mt-3 text-sm leading-6 theme-text-muted">
          Define a new companion profile, personality, tone, and greeting for
          the platform.
        </p>
      </section>

      <PageCard className="rounded-3xl">
        <SectionTitle>Basic Information</SectionTitle>

        <div className="mt-4 space-y-4">
          <div>
            <FieldLabel>Character Name</FieldLabel>
            <TextInput
              placeholder="Enter character name"
              value={form.name}
              onChange={(e) => updateField("name", e.target.value)}
            />
          </div>

          <div>
            <FieldLabel>Short Tagline</FieldLabel>
            <TextInput
              placeholder="e.g. Warm, supportive companion"
              value={form.tagline}
              onChange={(e) => updateField("tagline", e.target.value)}
            />
          </div>

          <div>
            <FieldLabel>Avatar URL</FieldLabel>
            <TextInput
              placeholder="Paste avatar image URL"
              value={form.avatarUrl}
              onChange={(e) => updateField("avatarUrl", e.target.value)}
            />
          </div>
        </div>
      </PageCard>

      <PageCard className="rounded-3xl">
        <SectionTitle>Character Personality</SectionTitle>

        <div className="mt-4 space-y-4">
          <div>
            <FieldLabel>Personality Description</FieldLabel>
            <TextArea
              rows={5}
              placeholder="Describe the character’s personality, behavior, and emotional style..."
              value={form.personality}
              onChange={(e) => updateField("personality", e.target.value)}
            />
          </div>

          <div>
            <FieldLabel>Greeting Message</FieldLabel>
            <TextArea
              rows={4}
              placeholder="Write the first message this character sends to users..."
              value={form.greeting}
              onChange={(e) => updateField("greeting", e.target.value)}
            />
          </div>
        </div>
      </PageCard>

      <PageCard className="rounded-3xl">
        <SectionTitle>Configuration</SectionTitle>

        <div className="mt-4 grid grid-cols-1 gap-4">
          <div>
            <FieldLabel>Tone / Style</FieldLabel>
            <SelectInput
              value={form.tone}
              onChange={(e) => updateField("tone", e.target.value)}
            >
              <option>Warm</option>
              <option>Playful</option>
              <option>Romantic</option>
              <option>Reflective</option>
              <option>Bold</option>
            </SelectInput>
          </div>

          <div>
            <FieldLabel>Language</FieldLabel>
            <SelectInput
              value={form.language}
              onChange={(e) => updateField("language", e.target.value)}
            >
              <option>English</option>
              <option>Swahili</option>
              <option>Sheng</option>
            </SelectInput>
          </div>

          <div>
            <FieldLabel>Status</FieldLabel>
            <SelectInput
              value={form.status}
              onChange={(e) => updateField("status", e.target.value)}
            >
              <option>Draft</option>
              <option>Published</option>
              <option>Archived</option>
            </SelectInput>
          </div>
        </div>
      </PageCard>

      <PageCard className="rounded-3xl">
        <SectionTitle>Feature Controls</SectionTitle>

        <div className="mt-4 space-y-3">
          <ToggleItem
            title="Enable NSFW Mode"
            subtitle="Keep disabled by default unless explicitly allowed."
            checked={form.nsfwEnabled}
            onChange={() => updateField("nsfwEnabled", !form.nsfwEnabled)}
          />
        </div>
      </PageCard>

      <PageCard className="rounded-3xl">
        <SectionTitle>Preview</SectionTitle>

        <div
          className="mt-4 rounded-3xl border p-4"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-soft)",
          }}
        >
          <div className="flex items-start gap-3">
            <div
              className="flex h-14 w-14 items-center justify-center rounded-full text-base font-semibold"
              style={{
                backgroundColor: "var(--color-accent-soft)",
                color: "var(--color-accent)",
              }}
            >
              {(form.name || "C").charAt(0).toUpperCase()}
            </div>

            <div className="min-w-0 flex-1">
              <h3 className="text-base font-semibold theme-text">
                {form.name || "Character Name"}
              </h3>

              <p className="mt-1 text-sm theme-text-muted">
                {form.tagline || "Short character tagline"}
              </p>

              <div className="mt-3 flex flex-wrap gap-2">
                {[form.tone, form.language, form.status].map((item) => (
                  <span
                    key={item}
                    className="rounded-full px-2.5 py-1 text-[11px]"
                    style={{
                      backgroundColor: "var(--color-card)",
                      color: "var(--color-text-muted)",
                      border: "1px solid var(--color-border)",
                    }}
                  >
                    {item}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <p className="mt-4 text-sm leading-6 theme-text-muted">
            {form.personality || "Personality preview will appear here."}
          </p>

          <div
            className="mt-4 rounded-2xl p-3"
            style={{
              backgroundColor: "var(--color-card-soft)",
              border: "1px solid var(--color-border)",
            }}
          >
            <p className="text-xs uppercase tracking-wide theme-text-muted">
              Greeting Preview
            </p>
            <p className="mt-2 text-sm theme-text">
              {form.greeting || "Greeting message preview will appear here."}
            </p>
          </div>
        </div>
      </PageCard>

      <div className="grid grid-cols-2 gap-3 pb-4">
        <PrimaryButton variant="secondary" fullWidth>
          Save Draft
        </PrimaryButton>
        <PrimaryButton fullWidth>Publish Character</PrimaryButton>
      </div>
    </div>
  );
}
