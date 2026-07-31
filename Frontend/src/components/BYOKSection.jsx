// Frontend/src/components/BYOKSection.jsx

import { useEffect, useState } from "react";
import { Eye, EyeOff, KeyRound, Trash2, AlertTriangle } from "lucide-react";
import { useLanguage } from "../context/LanguageContext";
import {
  getBYOKConfig,
  saveBYOKConfig,
  deleteBYOKConfig,
  toggleBYOK,
} from "../services/byokService";

function BYOKField({
  label,
  value,
  onChange,
  type = "text",
  disabled = false,
  placeholder = "",
}) {
  return (
    <div className="space-y-1">
      <label className="block text-xs font-semibold theme-text-muted uppercase tracking-wide">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder={placeholder}
        autoComplete="off"
        className="w-full rounded-xl border px-3 py-2 text-sm theme-text theme-card disabled:opacity-50 focus:outline-none focus:ring-2"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-soft)",
          "--tw-ring-color": "var(--color-accent)",
        }}
      />
    </div>
  );
}

export default function BYOKSection() {
  const { t } = useLanguage();

  const [savedConfig, setSavedConfig] = useState(null);
  const [apiUrl, setApiUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [storeOnPlatform, setStoreOnPlatform] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isToggling, setIsToggling] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  const hasExistingConfig = Boolean(savedConfig);
  const isActive = savedConfig?.is_active ?? false;
  const apiFieldsFilled = hasExistingConfig
    ? apiUrl.trim() && modelName.trim()
    : apiUrl.trim() && apiKey.trim() && modelName.trim();

  // ── Load on mount ────────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const response = await getBYOKConfig();
        if (response.ok) {
          const json = await response.json();
          const cfg = json?.data;
          if (cfg) {
            setSavedConfig(cfg);
            setApiUrl(cfg.api_url || "");
            setModelName(cfg.model_name || "");
            setSystemPrompt(cfg.system_prompt || "");
            setStoreOnPlatform(cfg.store_on_platform ?? false);
          }
        }
        // 404 = no config yet, ignore silently
      } catch {
        // network error — ignore silently
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  const clearMessages = () => {
    setError("");
    setSuccess("");
  };

  const mapError = (code) => {
    const map = {
      invalid_api_key: t("byok.errorInvalidKey"),
      model_not_found: t("byok.errorModelNotFound"),
      connection_error: t("byok.errorConnection"),
      access_forbidden: t("byok.errorForbidden"),
      api_url_api_key_and_model_name_required: t("byok.errorAllFieldsRequired"),
      system_prompt_requires_api_key: t("byok.errorSystemPromptRequiresKey"),
      validation_failed: t("byok.errorValidationFailed"),
    };
    return map[code] || t("byok.errorGeneric");
  };

  // ── Save ──────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    clearMessages();
    if (
      !apiUrl.trim() ||
      (!hasExistingConfig && !apiKey.trim()) ||
      !modelName.trim()
    ) {
      setError(t("byok.errorAllFieldsRequired"));
      return;
    }
    setIsSaving(true);
    try {
      const response = await saveBYOKConfig({
        api_url: apiUrl.trim(),
        api_key: apiKey.trim(),
        model_name: modelName.trim(),
        system_prompt: systemPrompt.trim(),
        store_on_platform: storeOnPlatform,
      });
      const json = await response.json();
      if (response.ok && json?.status === "success") {
        setSavedConfig(json.data);
        setApiKey("");
        setSuccess(t("byok.saveSuccess"));
      } else {
        setError(mapError(json?.data));
      }
    } catch {
      setError(t("byok.errorGeneric"));
    } finally {
      setIsSaving(false);
    }
  };

  // ── Delete ────────────────────────────────────────────────────────────────
  const handleDelete = async () => {
    clearMessages();
    setIsDeleting(true);
    try {
      const response = await deleteBYOKConfig();
      if (response.ok) {
        setSavedConfig(null);
        setApiUrl("");
        setApiKey("");
        setModelName("");
        setSystemPrompt("");
        setStoreOnPlatform(false);
        setSuccess(t("byok.deleteSuccess"));
        setIsExpanded(false);
      } else {
        setError(t("byok.errorGeneric"));
      }
    } catch {
      setError(t("byok.errorGeneric"));
    } finally {
      setIsDeleting(false);
    }
  };

  // ── Toggle ────────────────────────────────────────────────────────────────
  const handleToggle = async () => {
    if (!hasExistingConfig) return;
    clearMessages();
    setIsToggling(true);
    try {
      const response = await toggleBYOK(!isActive);
      if (response.ok) {
        setSavedConfig((prev) => ({ ...prev, is_active: !isActive }));
        if (isActive) setSuccess(t("byok.disabledPlatformFallback"));
      } else {
        setError(t("byok.errorGeneric"));
      }
    } catch {
      setError(t("byok.errorGeneric"));
    } finally {
      setIsToggling(false);
    }
  };

  if (isLoading) return null;

  return (
    <div
      className="rounded-2xl border overflow-hidden"
      style={{ borderColor: "var(--color-border)" }}
    >
      {/* ── Header row ─────────────────────────────────────────────────── */}
      <button
        type="button"
        onClick={() => {
          clearMessages();
          setIsExpanded((v) => !v);
        }}
        className="w-full flex items-center justify-between gap-4 p-4 text-left transition hover:opacity-90 theme-card"
      >
        <div className="flex items-center gap-3">
          <div
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border"
            style={{
              backgroundColor: "var(--color-soft)",
              borderColor: "var(--color-border)",
              color: "var(--color-accent)",
            }}
          >
            <KeyRound size={18} />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold theme-text">
              {t("byok.title")}
            </p>
            <p className="mt-0.5 text-sm theme-text-muted">
              {hasExistingConfig
                ? `${savedConfig.model_name} · ${savedConfig.api_key_hint} · ${isActive ? t("byok.active") : t("byok.inactive")}`
                : t("byok.subtitle")}
            </p>
          </div>
        </div>

        {/* Active toggle — only shown when config exists */}
        {hasExistingConfig && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              handleToggle();
            }}
            disabled={isToggling}
            className={`relative h-7 w-12 shrink-0 rounded-full transition disabled:opacity-50 ${
              isActive ? "bg-[var(--color-accent)]" : "bg-slate-500/60"
            }`}
            aria-pressed={isActive}
            title={isActive ? t("byok.disable") : t("byok.enable")}
          >
            <span
              className={`absolute top-1 h-5 w-5 rounded-full bg-white transition ${
                isActive ? "left-6" : "left-1"
              }`}
            />
          </button>
        )}
      </button>

      {/* ── Expanded form ──────────────────────────────────────────────── */}
      {isExpanded && (
        <div
          className="border-t p-4 space-y-4"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-shell)",
          }}
        >
          {/* Cost banner */}
          <div
            className="flex items-start gap-2 rounded-xl p-3 text-sm"
            style={{
              backgroundColor: "var(--color-accent-soft)",
              color: "var(--color-accent)",
              border: "1px solid var(--color-border)",
            }}
          >
            <span className="mt-0.5 shrink-0">💰</span>
            <span dangerouslySetInnerHTML={{ __html: t("byok.costBanner") }} />
          </div>

          {/* Inactive hint */}
          {hasExistingConfig && !isActive && (
            <div
              className="flex items-start gap-2 rounded-xl p-3 text-xs"
              style={{
                backgroundColor: "rgba(100,116,139,0.08)",
                border: "1px solid rgba(100,116,139,0.2)",
                color: "var(--color-text-muted)",
              }}
            >
              <span className="mt-0.5 shrink-0">ℹ️</span>
              <span>{t("byok.inactiveHint")}</span>
            </div>
          )}

          {/* API URL */}
          <BYOKField
            label={t("byok.apiUrl")}
            value={apiUrl}
            onChange={setApiUrl}
            placeholder="https://api.cortecs.ai/v1"
          />

          {/* API Key */}
          <div className="space-y-1">
            <label className="block text-xs font-semibold theme-text-muted uppercase tracking-wide">
              {t("byok.apiKey")}
              {hasExistingConfig && savedConfig.api_key_hint && (
                <span
                  className="ml-2 font-mono text-xs"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  ({t("byok.currentKey")}: {savedConfig.api_key_hint})
                </span>
              )}
            </label>
            <div className="relative">
              <input
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={
                  hasExistingConfig
                    ? t("byok.leaveBlankToKeep")
                    : t("byok.apiKeyPlaceholder")
                }
                autoComplete="new-password"
                className="w-full rounded-xl border px-3 py-2 pr-10 text-sm theme-text theme-card focus:outline-none focus:ring-2"
                style={{
                  borderColor: "var(--color-border)",
                  backgroundColor: "var(--color-soft)",
                  "--tw-ring-color": "var(--color-accent)",
                }}
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 theme-text-muted hover:opacity-80"
              >
                {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          {/* Model name */}
          <BYOKField
            label={t("byok.modelName")}
            value={modelName}
            onChange={setModelName}
            placeholder="deepseek-v4-flash"
          />

          {/* System prompt */}
          <div className="space-y-1">
            <label className="block text-xs font-semibold theme-text-muted uppercase tracking-wide">
              {t("byok.systemPrompt")}
              <span className="ml-1 font-normal normal-case">
                ({t("common.optional")})
              </span>
            </label>
            <div
              className="flex items-start gap-2 rounded-xl p-3 text-xs mb-2"
              style={{
                backgroundColor: "rgba(245, 158, 11, 0.08)",
                border: "1px solid rgba(245, 158, 11, 0.25)",
                color: "rgb(180, 120, 0)",
              }}
            >
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>{t("byok.systemPromptWarning")}</span>
            </div>
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              disabled={!apiFieldsFilled}
              rows={4}
              placeholder={
                apiFieldsFilled
                  ? t("byok.systemPromptPlaceholder")
                  : t("byok.systemPromptDisabledHint")
              }
              className="w-full rounded-xl border px-3 py-2 text-sm theme-text theme-card disabled:opacity-40 focus:outline-none focus:ring-2 resize-none"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-soft)",
                "--tw-ring-color": "var(--color-accent)",
              }}
            />
          </div>

          {/* Store on platform */}
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={storeOnPlatform}
              onChange={(e) => setStoreOnPlatform(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded accent-[var(--color-accent)]"
            />
            <div>
              <p className="text-sm font-medium theme-text">
                {t("byok.storeOnPlatform")}
              </p>
              <p className="text-xs theme-text-muted mt-0.5">
                {t("byok.storeOnPlatformHint")}
              </p>
            </div>
          </label>

          {/* Messages */}
          {error && (
            <p
              className="text-sm text-red-500 rounded-xl p-2"
              style={{ backgroundColor: "rgba(244,63,94,0.08)" }}
            >
              {error}
            </p>
          )}
          {success && (
            <p
              className="text-sm rounded-xl p-2"
              style={{
                color: "var(--color-accent)",
                backgroundColor: "var(--color-accent-soft)",
              }}
            >
              {success}
            </p>
          )}

          {/* Buttons */}
          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={handleSave}
              disabled={isSaving || !apiUrl.trim() || !modelName.trim()}
              className="flex-1 rounded-xl px-4 py-2 text-sm font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed"
              style={{
                backgroundColor: "var(--color-accent)",
                color: "#082f49",
              }}
            >
              {isSaving ? t("common.saving") : t("byok.save")}
            </button>

            {hasExistingConfig && (
              <button
                type="button"
                onClick={handleDelete}
                disabled={isDeleting}
                className="flex items-center gap-2 rounded-xl border px-4 py-2 text-sm font-semibold transition disabled:opacity-50"
                style={{
                  borderColor: "rgba(244,63,94,0.35)",
                  color: "rgb(225,29,72)",
                  backgroundColor: "rgba(244,63,94,0.06)",
                }}
              >
                <Trash2 size={15} />
                {isDeleting ? t("common.deleting") : t("byok.remove")}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
