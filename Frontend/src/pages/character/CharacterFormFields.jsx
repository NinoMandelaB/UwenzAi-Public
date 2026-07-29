import { forwardRef } from "react";
import { Check } from "lucide-react";
import {
  TONE_OPTIONS,
  LANGUAGE_OPTIONS,
  CATEGORY_OPTIONS,
} from "./characterFormConstants";

export function FieldError({ message }) {
  if (!message) return null;

  return <p className="mt-2 text-xs text-rose-400">{message}</p>;
}

export const TextInput = forwardRef(function TextInput(
  {
    label,
    value,
    onChange,
    placeholder,
    maxLength,
    error,
    required = false,
    helperText,
    ...props
  },
  ref,
) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm font-medium theme-text">
        {label} {required && <span className="text-rose-400">*</span>}
      </span>

      <input
        ref={ref}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        maxLength={maxLength}
        className="w-full rounded-2xl border px-4 py-3 text-sm outline-none transition theme-card theme-text placeholder:text-slate-400 focus:ring-2"
        style={{
          borderColor: error ? "rgba(244, 63, 94, 0.6)" : "var(--color-border)",
          backgroundColor: "var(--color-surface, transparent)",
          boxShadow: error ? "0 0 0 1px rgba(244, 63, 94, 0.15)" : "none",
        }}
        {...props}
      />

      {helperText && (
        <p className="mt-2 text-xs theme-text-muted">{helperText}</p>
      )}

      {maxLength ? (
        <div className="mt-2 flex items-center justify-between">
          <FieldError message={error} />
          <span className="text-xs theme-text-muted">
            {value.length}/{maxLength}
          </span>
        </div>
      ) : (
        <FieldError message={error} />
      )}
    </label>
  );
});

export const TextArea = forwardRef(function TextArea(
  {
    label,
    value,
    onChange,
    placeholder,
    rows = 4,
    error,
    required = false,
    helperText,
    maxLength,
    ...props
  },
  ref,
) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm font-medium theme-text">
        {label} {required && <span className="text-rose-400">*</span>}
      </span>

      <textarea
        ref={ref}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        rows={rows}
        maxLength={maxLength}
        className="w-full rounded-2xl border px-4 py-3 text-sm outline-none transition theme-card theme-text placeholder:text-slate-400 focus:ring-2"
        style={{
          borderColor: error ? "rgba(244, 63, 94, 0.6)" : "var(--color-border)",
          backgroundColor: "var(--color-surface, transparent)",
          boxShadow: error ? "0 0 0 1px rgba(244, 63, 94, 0.15)" : "none",
          resize: "vertical",
        }}
        {...props}
      />

      {helperText && (
        <p className="mt-2 text-xs theme-text-muted">{helperText}</p>
      )}

      {maxLength ? (
        <div className="mt-2 flex items-center justify-between">
          <FieldError message={error} />
          <span className="text-xs theme-text-muted">
            {value.length}/{maxLength}
          </span>
        </div>
      ) : (
        <FieldError message={error} />
      )}
    </label>
  );
});

function ChoicePills({
  label,
  value,
  onChange,
  error,
  options,
  required = false,
}) {
  return (
    <div>
      <span className="mb-2 block text-sm font-medium theme-text">
        {label} {required && <span className="text-rose-400">*</span>}
      </span>

      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const isSelected = value === option.value;

          return (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange(option.value)}
              className="inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition-all duration-150 hover:opacity-90 active:scale-95"
              style={{
                backgroundColor: isSelected
                  ? "var(--color-accent-soft)"
                  : "var(--color-soft)",
                color: isSelected
                  ? "var(--color-accent)"
                  : "var(--color-text-muted)",
                border: isSelected
                  ? "1px solid var(--color-accent)"
                  : "1px solid var(--color-border)",
                boxShadow: isSelected
                  ? "0 0 0 1px rgba(34,211,238,0.35), 0 6px 18px rgba(34,211,238,0.12)"
                  : "none",
              }}
              aria-pressed={isSelected}
            >
              {isSelected && <Check size={16} className="shrink-0" />}
              <span>{option.label}</span>
            </button>
          );
        })}
      </div>

      <FieldError message={error} />
    </div>
  );
}

export function LanguagePills({ value, onChange, error }) {
  return (
    <ChoicePills
      label="Primary Language"
      value={value}
      onChange={onChange}
      error={error}
      options={LANGUAGE_OPTIONS}
      required
    />
  );
}

export function TonePills({ value, onChange, error }) {
  return (
    <ChoicePills
      label="Tone Style"
      value={value}
      onChange={onChange}
      error={error}
      options={TONE_OPTIONS}
      required
    />
  );
}

export function CategoryPills({ value, onChange, error }) {
  return (
    <ChoicePills
      label="Category"
      value={value}
      onChange={onChange}
      error={error}
      options={CATEGORY_OPTIONS}
      required
    />
  );
}
