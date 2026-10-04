import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownLeft,
  ArrowUpRight,
  BookOpen,
  Check,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  LockKeyhole,
  MessageSquare,
  Plus,
  RotateCcw,
  Send,
  ShieldCheck,
  Square,
  Terminal,
  Trash2,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { workspaceText } from "../i18n/developerWorkspace";
import { LEGAL_POLICY_VERSION } from "../i18n/legal";
import {
  listAgentKeys,
  createAgentKey,
  revokeAgentKey,
  getAgentUsage,
  getConsent,
  confirmConsent,
  getAgentPricing,
  playgroundCompletion,
  getPlaygroundHistory,
  deletePlaygroundHistory,
} from "../services/agentDevService";
import { reuseOperationId, clearOperationId } from "../utils/idempotency";
import {
  MAX_PROMPT_CHARS,
  workspaceLimits,
  estimateTokens,
  validMessages,
  messageLimitError,
  boundedDisplay,
  okData,
  errorText,
  connectionCode,
} from "./developerWorkspaceUtils";

// Public API documentation site linked from the workspace.
const DEV_DOCS_URL =
  import.meta.env.VITE_DEV_DOCS_URL || "https://uwenzai.com/uwenzai-dev/docs/";
import "./UwenzAiDev.css";

// Each mounted scope owns its work. Account and pair boundaries unmount scopes;
// even an API/mock that ignores AbortSignal cannot write late results to the UI.
function useRequestScope() {
  const controllers = useRef(new Set());
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controllers.current.forEach((controller) => controller.abort());
      controllers.current.clear();
    };
  }, []);
  return () => {
    const controller = new AbortController();
    controllers.current.add(controller);
    return {
      signal: controller.signal,
      current: () => alive.current && !controller.signal.aborted,
      abort: () => controller.abort(),
      done: () => controllers.current.delete(controller),
    };
  };
}

function InlineError({ code, tx, retry }) {
  if (!code) return null;
  return (
    <div className="dev-error" role="alert">
      <span>{tx(code)}</span>
      {retry && (
        <button type="button" className="dev-button" onClick={retry}>
          {tx("retry")}
        </button>
      )}
    </div>
  );
}
function CopyButton({ value, label, tx }) {
  const [state, setState] = useState("");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    setState("");
  }, [value]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      if (mounted.current) setState("copied");
    } catch {
      if (mounted.current) setState("copyFailed");
    }
  };
  return (
    <span className="dev-copy">
      <button
        type="button"
        className="dev-button dev-icon-button"
        aria-label={label}
        title={label}
        onClick={copy}
        disabled={!value}
      >
        {state === "copied" ? <Check size={16} /> : <Copy size={16} />}
      </button>
      {state && (
        <span
          className={
            state === "copyFailed"
              ? "dev-copy-feedback dev-error-text"
              : "dev-copy-feedback"
          }
          role="status"
        >
          {tx(state)}
        </span>
      )}
    </span>
  );
}

export default function UwenzAiDev() {
  const { isLoading, isAuthenticated, user, accessToken, sessionGeneration } =
    useAuth();
  const { language } = useLanguage();
  const tx = workspaceText(language);
  // Hydration is not a failed login. Normal token refresh never changes this key.
  if (isLoading || (isAuthenticated && !accessToken))
    return (
      <div className="dev-workspace dev-loading" role="status">
        <Terminal size={24} />
        <p>{tx("authenticating")}</p>
      </div>
    );
  if (!isAuthenticated || !user)
    return (
      <div className="dev-workspace dev-loading">
        <LockKeyhole size={24} />
        <h1>{tx("title")}</h1>
        <p>{tx("login")}</p>
      </div>
    );
  return (
    <Workspace
      key={`${user.user_id ?? user.id}:${sessionGeneration ?? 0}`}
      language={language}
    />
  );
}

function Workspace({ language }) {
  const tx = workspaceText(language);
  const requestScope = useRequestScope();
  const [pricing, setPricing] = useState(null);
  const [pricingBusy, setPricingBusy] = useState(true);
  const [pricingError, setPricingError] = useState("");
  const [model, setModel] = useState("");
  const [provider, setProvider] = useState("");
  const [consent, setConsent] = useState("checking");
  const [consentError, setConsentError] = useState("");
  const [tab, setTab] = useState("setup");
  const [keys, setKeys] = useState([]);
  const [keysBusy, setKeysBusy] = useState(false);
  const [keysError, setKeysError] = useState("");
  const [keyId, setKeyId] = useState("");
  const [usage, setUsage] = useState(null);
  const [usageBusy, setUsageBusy] = useState(false);
  const [usageError, setUsageError] = useState("");
  const [lastRequest, setLastRequest] = useState(null);
  const usageRequest = useRef(null);
  const keysRequest = useRef(null);
  const consentRequest = useRef(null);

  async function loadPricing() {
    const task = requestScope();
    setPricingBusy(true);
    setPricingError("");
    try {
      const data = okData(await getAgentPricing({ signal: task.signal }));
      if (
        !Array.isArray(data.models) ||
        typeof data.base_url !== "string" ||
        !/^https?:\/\//.test(data.base_url)
      )
        throw new Error("invalid_response");
      if (task.current()) setPricing(data);
    } catch (error) {
      if (task.current()) setPricingError(errorText(error, "pricingFailed"));
    } finally {
      if (task.current()) setPricingBusy(false);
      task.done();
    }
  }
  async function checkConsent(accept = false) {
    if (consentRequest.current) return;
    const task = requestScope();
    consentRequest.current = task;
    setConsent(accept ? "saving" : "checking");
    setConsentError("");
    try {
      const data = okData(
        await (accept ? confirmConsent : getConsent)({ signal: task.signal }),
      );
      if (data.version !== LEGAL_POLICY_VERSION)
        throw Object.assign(new Error("consent_version_mismatch"), {
          code: "consent_version_mismatch",
        });
      if (typeof data.confirmed !== "boolean" || (accept && !data.confirmed))
        throw new Error("consent_failed");
      if (task.current()) setConsent(data.confirmed ? "confirmed" : "required");
    } catch (error) {
      if (task.current()) {
        setConsent("failed");
        setConsentError(
          error.code === "consent_version_mismatch"
            ? "consentVersionMismatch"
            : "consentFailed",
        );
      }
    } finally {
      if (consentRequest.current === task) consentRequest.current = null;
      task.done();
    }
  }
  async function loadKeys() {
    keysRequest.current?.abort();
    const task = requestScope();
    keysRequest.current = task;
    setKeysBusy(true);
    setKeysError("");
    try {
      const data = okData(await listAgentKeys({ signal: task.signal }));
      if (!Array.isArray(data.keys)) throw new Error("invalid_response");
      if (task.current()) {
        setKeys(data.keys);
        setKeyId((current) =>
          data.keys.some(
            (key) =>
              String(key.id) === current && key.is_active && !key.is_expired,
          )
            ? current
            : "",
        );
      }
    } catch (error) {
      if (task.current()) setKeysError(errorText(error, "keyFailed"));
    } finally {
      if (task.current()) setKeysBusy(false);
      task.done();
    }
  }
  async function loadUsage() {
    usageRequest.current?.abort();
    const task = requestScope();
    usageRequest.current = task;
    setUsageBusy(true);
    setUsageError("");
    try {
      const data = okData(await getAgentUsage({ signal: task.signal }));
      if (task.current()) setUsage(data);
    } catch (error) {
      if (task.current()) setUsageError(errorText(error, "usageFailed"));
    } finally {
      if (task.current()) setUsageBusy(false);
      task.done();
    }
  }
  useEffect(() => {
    loadPricing();
    checkConsent();
    return () => {
      consentRequest.current = null;
    };
  }, []);
  useEffect(() => {
    if (consent === "confirmed") {
      loadKeys();
      loadUsage();
    }
  }, [consent]);
  const models = pricing?.models || [];
  const selected = models.find(
    (pair) => pair.model === model && pair.provider === provider,
  );
  const pair = selected?.available === true ? selected : null;
  const modelOptions = [
    ...new Set(
      models
        .filter((p) => !provider || p.provider === provider)
        .map((p) => p.model),
    ),
  ];
  const providerOptions = [
    ...new Set(
      models.filter((p) => !model || p.model === model).map((p) => p.provider),
    ),
  ];
  // Optional provider metadata from the pricing API; older responses have none.
  const providerLabel = (name) => {
    const meta = pricing?.providers?.[name];
    const country = typeof meta?.country === "string" ? meta.country : "";
    if (!country) return name;
    const kindLabel =
      typeof meta?.kind === "string" &&
      tx(`providerKind.${meta.kind}`) !== `providerKind.${meta.kind}`
        ? ` · ${tx(`providerKind.${meta.kind}`)}`
        : "";
    return `${name} — ${country}${kindLabel}`;
  };
  const pairKey = JSON.stringify([model, provider]);
  const format = (value, digits = 2) =>
    value === null || value === undefined || !Number.isFinite(Number(value))
      ? tx("notSpecified")
      : Number(value).toLocaleString(language === "sw" ? "sw-KE" : "en-KE", {
          maximumFractionDigits: digits,
          minimumFractionDigits: digits,
        });
  function chooseModel(value) {
    setModel(value);
  }
  function chooseProvider(value) {
    setProvider(value);
  }

  return (
    <div className="dev-workspace">
      <header className="dev-heading">
        <div>
          <p className="dev-eyebrow">
            <Terminal size={15} />
            {tx("eyebrow")}
          </p>
          <h1>
            {tx("title")}{" "}
            <span className="dev-badge">{tx("experimental")}</span>
          </h1>
          <p className="dev-muted">{tx("subtitle")}</p>
        </div>
      </header>
      <p className="dev-experimental">{tx("experimentalNote")}</p>
      <div className="dev-surface">
        <section className="dev-model-section" aria-label={tx("pricing")}>
          <div className="dev-selectors">
            <label className="dev-field">
              <span>01 / {tx("model")}</span>
              <select
                aria-label={tx("model")}
                value={model}
                onChange={(e) => chooseModel(e.target.value)}
                disabled={pricingBusy || !models.length}
              >
                <option value="">{tx("allModels")}</option>
                {modelOptions.map((name) => (
                  <option
                    key={name}
                    value={name}
                    disabled={
                      !models.some(
                        (p) =>
                          p.model === name &&
                          (!provider || p.provider === provider) &&
                          p.available === true,
                      )
                    }
                  >
                    {name}
                    {!models.some(
                      (p) =>
                        p.model === name &&
                        (!provider || p.provider === provider) &&
                        p.available === true,
                    )
                      ? ` — ${tx("unavailable")}`
                      : ""}
                  </option>
                ))}
              </select>
            </label>
            <span className="dev-selector-link" aria-hidden="true">
              ↔
            </span>
            <label className="dev-field">
              <span>02 / {tx("provider")}</span>
              <select
                aria-label={tx("provider")}
                value={provider}
                onChange={(e) => chooseProvider(e.target.value)}
                disabled={pricingBusy || !models.length}
              >
                <option value="">{tx("allProviders")}</option>
                {providerOptions.map((name) => (
                  <option
                    key={name}
                    value={name}
                    disabled={
                      !models.some(
                        (p) =>
                          p.provider === name &&
                          (!model || p.model === model) &&
                          p.available === true,
                      )
                    }
                  >
                    {providerLabel(name)}
                    {!models.some(
                      (p) =>
                        p.provider === name &&
                        (!model || p.model === model) &&
                        p.available === true,
                    )
                      ? ` — ${tx("unavailable")}`
                      : ""}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="dev-button dev-reset"
              onClick={() => {
                chooseModel("");
                chooseProvider("");
              }}
              disabled={!model && !provider}
            >
              <RotateCcw size={15} />
              {tx("reset")}
            </button>
          </div>
          <InlineError code={pricingError} tx={tx} retry={loadPricing} />
          {pricingBusy ? (
            <div className="dev-skeleton" role="status">
              {tx("loading")}
            </div>
          ) : !models.length && !pricingError ? (
            <p className="dev-muted">{tx("emptyModels")}</p>
          ) : !pair ? (
            <p className="dev-selection-hint">{tx("selectHint")}</p>
          ) : (
            <>
              <div className="dev-price-strip">
                <div>
                  <p className="dev-label">{tx("pricing")}</p>
                  <span className="dev-status">
                    <span />
                    {tx("available")}
                  </span>
                </div>
                <div className="dev-price">
                  <span>
                    <ArrowDownLeft size={16} />
                    {tx("input")}
                  </span>
                  <strong data-testid="input-sparks-price">
                    {format(pair.sparks_per_1m_input, 0)} {tx("sparks")}
                  </strong>
                  <small
                    data-testid="input-kes-price"
                    className="dev-price-sub"
                  >
                    KES {format(pair.input_kes_per_1m)}
                  </small>
                </div>
                <div className="dev-price">
                  <span>
                    <ArrowUpRight size={16} />
                    {tx("output")}
                  </span>
                  <strong data-testid="output-sparks-price">
                    {format(pair.sparks_per_1m_output, 0)} {tx("sparks")}
                  </strong>
                  <small
                    data-testid="output-kes-price"
                    className="dev-price-sub"
                  >
                    KES {format(pair.output_kes_per_1m)}
                  </small>
                </div>
              </div>
              <p className="dev-footnote">
                {tx("priceNote", {
                  markup: format(
                    pair && pair.markup_percent !== undefined
                      ? pair.markup_percent
                      : pricing.markup_percent,
                    1,
                  ),
                  value: format(pricing.spark_value_kes),
                })}
              </p>
              <details className="dev-model-details">
                <summary>{tx("details")}</summary>
                <dl>
                  <div>
                    <dt>{tx("context")}</dt>
                    <dd>{format(pair.max_context, 0)}</dd>
                  </div>
                  <div>
                    <dt>{tx("maxOutput")}</dt>
                    <dd>{format(pair.max_output_tokens, 0)}</dd>
                  </div>
                  <div>
                    <dt>{tx("capabilities")}</dt>
                    <dd>
                      {pair.capabilities?.length
                        ? pair.capabilities
                            .filter((value) => typeof value === "string")
                            .map((value) =>
                              tx(`capability_${value}`) ===
                              `capability_${value}`
                                ? value
                                : tx(`capability_${value}`),
                            )
                            .join(", ")
                        : tx("notSpecified")}
                    </dd>
                  </div>
                  <div>
                    <dt>{tx("description")}</dt>
                    <dd>{pair.description || tx("notSpecified")}</dd>
                  </div>
                  <div>
                    <dt>
                      {tx("perMillion")} · {tx("input")} / {tx("output")}
                    </dt>
                    <dd>
                      {format(pair.sparks_per_1m_input, 0)} /{" "}
                      {format(pair.sparks_per_1m_output, 0)} {tx("sparks")} ·
                      KES {format(pair.input_kes_per_1m)} / KES{" "}
                      {format(pair.output_kes_per_1m)}
                    </dd>
                  </div>
                </dl>
              </details>
            </>
          )}
        </section>
        {consent !== "confirmed" && (
          <section className="dev-consent" aria-labelledby="dev-consent-title">
            <ShieldCheck size={22} />
            <div>
              <h2 id="dev-consent-title">{tx("consentTitle")}</h2>
              {["checking", "saving"].includes(consent) ? (
                <p role="status">
                  {tx(
                    consent === "saving" ? "consentSaving" : "consentChecking",
                  )}
                </p>
              ) : (
                <>
                  <p>{tx("consentBody")}</p>
                  <p className="dev-footnote">
                    {tx("consentVersion", { version: LEGAL_POLICY_VERSION })}{" "}
                    <a
                      className="dev-legal-link"
                      href="/terms"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {tx("termsOfUse")}
                    </a>
                    {" · "}
                    <a
                      className="dev-legal-link"
                      href="/privacy"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {tx("privacyPolicy")}
                    </a>
                    {" · "}
                    <a
                      className="dev-legal-link"
                      href="/subprocessors"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {tx("providerDisclosure")}
                    </a>
                  </p>
                  <p>{tx("consentCost")}</p>
                  <InlineError
                    code={consentError}
                    tx={tx}
                    retry={() => checkConsent()}
                  />
                  <button
                    type="button"
                    className="dev-button dev-primary"
                    onClick={() => checkConsent(true)}
                  >
                    {tx("consentAccept")}
                  </button>
                </>
              )}
            </div>
          </section>
        )}
        <nav className="dev-mobile-tabs" aria-label={tx("title")}>
          {[
            { name: "setup", icon: Terminal },
            { name: "chat", icon: MessageSquare },
            { name: "usage", icon: Activity },
          ].map((item) => (
            <button
              type="button"
              key={item.name}
              aria-pressed={tab === item.name}
              className={tab === item.name ? "is-active" : ""}
              onClick={() => setTab(item.name)}
            >
              <item.icon size={17} />
              {tx(item.name)}
            </button>
          ))}
        </nav>
        <div className="dev-columns">
          <section
            className={`dev-connect dev-mobile-panel ${tab === "setup" ? "is-active" : ""}`}
            aria-labelledby="dev-connect-title"
          >
            <Connection
              key={`connection:${pairKey}`}
              pair={pair}
              baseUrl={pricing?.base_url}
              tx={tx}
            />
            {consent === "confirmed" ? (
              <KeyManager
                key={`keys:${pairKey}`}
                tx={tx}
                language={language}
                keys={keys}
                busy={keysBusy}
                error={keysError}
                reload={loadKeys}
                keyId={keyId}
                setKeyId={setKeyId}
              />
            ) : (
              <p className="dev-locked">
                <LockKeyhole size={18} />
                {tx("consentLocked")}
              </p>
            )}
          </section>
          <section
            className={`dev-playground dev-mobile-panel ${tab === "chat" ? "is-active" : ""}`}
            aria-labelledby="dev-chat-title"
          >
            <div className="dev-section-heading">
              <MessageSquare size={18} />
              <h2 id="dev-chat-title">{tx("playgroundTitle")}</h2>
            </div>
            <p className="dev-muted">{tx("playgroundHint")}</p>
            {consent === "confirmed" ? (
              <Playground
                key={pairKey}
                tx={tx}
                pair={pair}
                keyId={keyId}
                keys={keys}
                keysReady={!keysBusy && !keysError}
                setKeyId={setKeyId}
                limits={workspaceLimits(pricing, pair)}
                onComplete={(result) => {
                  setLastRequest({ ...result, model, provider });
                  loadUsage();
                }}
              />
            ) : (
              <p className="dev-locked">
                <LockKeyhole size={18} />
                {tx("consentLocked")}
              </p>
            )}
          </section>
        </div>
        <section
          className={`dev-usage dev-mobile-panel ${tab === "usage" ? "is-active" : ""}`}
          aria-label={tx("usageTitle")}
        >
          <Usage
            tx={tx}
            language={language}
            data={usage}
            last={lastRequest}
            busy={usageBusy}
            error={usageError}
            refresh={loadUsage}
            enabled={consent === "confirmed"}
          />
        </section>
      </div>
    </div>
  );
}

function Connection({ pair, baseUrl, tx }) {
  const [format, setFormat] = useState("python");
  const code = connectionCode(format, baseUrl, pair, tx("examplePrompt"));
  return (
    <div className="dev-connection">
      <div className="dev-section-heading">
        <Terminal size={18} />
        <h2 id="dev-connect-title">{tx("connection")}</h2>
      </div>
      <p className="dev-muted">{tx("connectionHint")}</p>
      <a
        className="dev-docs-link"
        href={DEV_DOCS_URL}
        target="_blank"
        rel="noopener noreferrer"
      >
        <BookOpen size={15} />
        {tx("docsLink")}
      </a>
      <dl className="dev-config-values">
        {[
          ["baseUrl", baseUrl],
          ["model", pair?.model],
          ["provider", pair?.provider],
        ].map(([name, value]) => (
          <div key={name}>
            <dt>{tx(name)}</dt>
            <dd>
              <code>{value || "—"}</code>
              <CopyButton
                value={value || ""}
                label={`${tx("copy")} ${tx(name)}`}
                tx={tx}
              />
            </dd>
          </div>
        ))}
      </dl>
      {code ? (
        <div className="dev-code-block">
          <div className="dev-code-toolbar">
            <div className="dev-segmented">
              {["python", "curl"].map((name) => (
                <button
                  type="button"
                  key={name}
                  aria-pressed={format === name}
                  onClick={() => setFormat(name)}
                >
                  {tx(name)}
                </button>
              ))}
            </div>
            <CopyButton value={code} label={tx("copyConfig")} tx={tx} />
          </div>
          <pre tabIndex={0}>
            <code>{code}</code>
          </pre>
        </div>
      ) : (
        <p className="dev-empty-code">{tx("pairRequired")}</p>
      )}
      <p className="dev-footnote">{tx("envHint")}</p>
    </div>
  );
}

function KeyManager({
  tx,
  language,
  keys,
  busy,
  error,
  reload,
  keyId,
  setKeyId,
}) {
  const scope = useRequestScope();
  const operation = useRef(null);
  const [creating, setCreating] = useState(false);
  const [label, setLabel] = useState("");
  const [secret, setSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [action, setAction] = useState("");
  const [actionError, setActionError] = useState("");
  const [revokeId, setRevokeId] = useState(null);
  const [notice, setNotice] = useState("");
  const date = (value) =>
    value && !Number.isNaN(new Date(value).getTime())
      ? new Date(value).toLocaleDateString(
          language === "sw" ? "sw-KE" : "en-KE",
          { year: "numeric", month: "short", day: "numeric" },
        )
      : tx("notSpecified");
  async function create(event) {
    event.preventDefault();
    if (operation.current) return;
    if (!label.trim()) {
      setActionError("keyLabelRequired");
      return;
    }
    const task = scope();
    operation.current = task;
    setAction("create");
    setActionError("");
    setNotice("");
    try {
      const data = okData(
        await createAgentKey({ label: label.trim() }, { signal: task.signal }),
      );
      if (!data.full_key || !data.key_id) throw new Error("invalid_response");
      if (task.current()) {
        setSecret(data.full_key);
        setShowSecret(false);
        setCreating(false);
        setLabel("");
        setKeyId(String(data.key_id));
        setNotice("keyCreated");
        await reload();
      }
    } catch (err) {
      if (task.current()) setActionError(errorText(err, "createFailed"));
    } finally {
      if (task.current()) setAction("");
      operation.current = null;
      task.done();
    }
  }
  async function revoke() {
    if (operation.current || revokeId === null) return;
    const task = scope();
    operation.current = task;
    setAction("revoke");
    setActionError("");
    setNotice("");
    try {
      okData(await revokeAgentKey(revokeId, { signal: task.signal }));
      if (task.current()) {
        if (String(revokeId) === keyId) setKeyId("");
        setSecret("");
        setRevokeId(null);
        setNotice("keyRevoked");
        await reload();
      }
    } catch (err) {
      if (task.current()) setActionError(errorText(err, "revokeFailed"));
    } finally {
      if (task.current()) setAction("");
      operation.current = null;
      task.done();
    }
  }
  return (
    <div className="dev-keys">
      <div className="dev-section-heading">
        <KeyRound size={18} />
        <h2>{tx("keys")}</h2>
        <button
          type="button"
          className="dev-button dev-new-key"
          onClick={() => {
            setCreating(true);
            setSecret("");
            setNotice("");
            setActionError("");
          }}
          disabled={!!action || busy || !!error}
        >
          <Plus size={15} />
          {tx("newKey")}
        </button>
      </div>
      <InlineError code={error} tx={tx} retry={reload} />
      <InlineError code={actionError} tx={tx} />
      {notice && (
        <p className="dev-success" role="status">
          {tx(notice)}
        </p>
      )}
      {creating && (
        <form className="dev-key-form" onSubmit={create}>
          <label className="dev-field">
            <span>{tx("keyLabel")}</span>
            <input
              value={label}
              maxLength={100}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={tx("keyPlaceholder")}
              autoFocus
              disabled={!!action}
            />
          </label>
          <div className="dev-actions">
            <button
              type="button"
              className="dev-button"
              disabled={!!action}
              onClick={() => {
                setCreating(false);
                setActionError("");
              }}
            >
              {tx("cancel")}
            </button>
            <button className="dev-button dev-primary" disabled={!!action}>
              {tx(action === "create" ? "creating" : "newKey")}
            </button>
          </div>
        </form>
      )}
      {secret && (
        <div className="dev-key-secret">
          <p>{tx("keyWarning")}</p>
          <label className="dev-field">
            <span>{tx("fullKey")}</span>
            <input
              aria-label={tx("fullKey")}
              type={showSecret ? "text" : "password"}
              value={secret}
              readOnly
              autoComplete="off"
            />
          </label>
          <div className="dev-actions">
            <button
              type="button"
              className="dev-button"
              onClick={() => setShowSecret(!showSecret)}
            >
              {showSecret ? <EyeOff size={16} /> : <Eye size={16} />}
              {tx(showSecret ? "hide" : "reveal")}
            </button>
            <CopyButton value={secret} label={tx("copyKey")} tx={tx} />
            <button
              type="button"
              className="dev-button"
              onClick={() => {
                setSecret("");
                setShowSecret(false);
                setNotice("");
              }}
            >
              {tx("dismissKey")}
            </button>
          </div>
        </div>
      )}
      {busy ? (
        <p role="status" className="dev-muted">
          {tx("loading")}
        </p>
      ) : !error && !keys.length ? (
        <p className="dev-muted">{tx("keyEmpty")}</p>
      ) : (
        <ul className="dev-key-list">
          {keys.map((key) => (
            <li key={key.id}>
              <div className="dev-key-row">
                <div className="dev-key-info">
                  <strong>{key.label || tx("unnamedKey")}</strong>
                  <code>{key.hint}</code>
                  <span className="dev-footnote">
                    {tx(
                      key.is_expired
                        ? "expired"
                        : key.is_active
                          ? "active"
                          : "revoked",
                    )}{" "}
                    · {tx("created")} {date(key.created_at)}
                  </span>
                  {key.last_used_at && (
                    <span className="dev-footnote">
                      {tx("lastUsed")} {date(key.last_used_at)}
                    </span>
                  )}
                  {key.monthly_spark_budget != null && (
                    <span className="dev-footnote">
                      {tx("budget")}: {key.monthly_spark_budget}
                    </span>
                  )}
                </div>
                <CopyButton
                  value={key.hint}
                  label={`${tx("copyHint")} ${key.label || tx("unnamedKey")}`}
                  tx={tx}
                />
                {key.is_active && (
                  <button
                    type="button"
                    className="dev-button dev-icon-button"
                    aria-label={`${tx("revoke")} ${key.label || tx("unnamedKey")}`}
                    title={tx("revoke")}
                    disabled={!!action}
                    onClick={() => {
                      setRevokeId(key.id);
                      setActionError("");
                    }}
                  >
                    <Trash2 size={16} />
                  </button>
                )}
              </div>
              {revokeId === key.id && (
                <div className="dev-revoke-confirm">
                  <p>{tx("revokeConfirm")}</p>
                  <div className="dev-actions">
                    <button
                      type="button"
                      className="dev-button"
                      disabled={!!action}
                      onClick={() => setRevokeId(null)}
                    >
                      {tx("cancel")}
                    </button>
                    <button
                      type="button"
                      className="dev-button dev-destructive"
                      disabled={!!action}
                      onClick={revoke}
                    >
                      {tx(action === "revoke" ? "revoking" : "revoke")}
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Playground({
  tx,
  pair,
  keyId,
  keys,
  keysReady,
  setKeyId,
  limits,
  onComplete,
}) {
  const scope = useRequestScope();
  const pending = useRef(null);
  const historyTask = useRef(null);
  const operationId = useRef(null);
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState([]);
  const [saveHistory, setSaveHistory] = useState(false);
  const [historyState, setHistoryState] = useState("off");
  const [historyError, setHistoryError] = useState("");
  const [deleteToOff, setDeleteToOff] = useState(false);
  const [sending, setSending] = useState(false);
  const [retryAsNew, setRetryAsNew] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [maxTokens, setMaxTokens] = useState(String(limits.max_output_tokens));
  const log = useRef(null);
  const activeKeys = keys.filter((key) => key.is_active && !key.is_expired);
  const keyReady =
    keysReady && activeKeys.some((key) => String(key.id) === keyId);
  const currentMessage = { role: "user", content: prompt.trim() };
  const requestMessages = saveHistory
    ? [...messages, currentMessage]
    : [currentMessage];
  const estimate = estimateTokens(requestMessages);
  const maxResponse = Number(maxTokens);
  const busyHistory = ["loading", "deleting"].includes(historyState);
  const historyBlocked =
    busyHistory || ["failed", "deleteFailed"].includes(historyState);
  const tooLong = messageLimitError(requestMessages, limits);
  const maxValid =
    Number.isInteger(maxResponse) &&
    maxResponse > 0 &&
    maxResponse <= limits.max_output_tokens;
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [messages, sending]);

  async function loadHistory() {
    if (!pair || pending.current) return;
    historyTask.current?.abort();
    const task = scope();
    historyTask.current = task;
    setSaveHistory(true);
    setHistoryState("loading");
    setHistoryError("");
    setError("");
    setNotice("");
    try {
      const data = okData(
        await getPlaygroundHistory(pair, { signal: task.signal }),
      );
      if (
        !validMessages(data.messages) ||
        messageLimitError(data.messages, limits)
      )
        throw new Error("history_limit");
      if (task.current()) {
        setMessages(
          data.messages.map(({ role, content }) => ({ role, content })),
        );
        setHistoryState("ready");
        clearOperationId(operationId);
      }
    } catch (err) {
      if (task.current()) {
        setHistoryError(errorText(err, "historyFailed"));
        setHistoryState("failed");
      }
    } finally {
      task.done();
    }
  }
  async function deleteHistory(turnOff = false) {
    // Never overlap a billed send with deletion. The server additionally checks
    // a history revision so a response finishing after a timed-out request
    // cannot resurrect history deleted by another tab/session.
    if (!pair || pending.current || historyState === "deleting") return;
    historyTask.current?.abort();
    const task = scope();
    historyTask.current = task;
    setDeleteToOff(turnOff);
    setHistoryState("deleting");
    setHistoryError("");
    setError("");
    setNotice("");
    try {
      okData(await deletePlaygroundHistory(pair, { signal: task.signal }));
      if (task.current()) {
        setMessages([]);
        setSaveHistory(turnOff ? false : saveHistory);
        setHistoryState(turnOff || !saveHistory ? "off" : "ready");
        setNotice("deleted");
        clearOperationId(operationId);
      }
    } catch (err) {
      if (task.current()) {
        setHistoryError(errorText(err, "deleteFailed"));
        setHistoryState("deleteFailed");
      }
    } finally {
      task.done();
    }
  }
  async function send(event) {
    event.preventDefault();
    if (
      pending.current ||
      !pair ||
      !keyReady ||
      historyBlocked ||
      !prompt.trim() ||
      !maxValid
    )
      return;
    if (
      prompt.length > MAX_PROMPT_CHARS ||
      messageLimitError([currentMessage], limits)
    ) {
      setError("promptTooLong");
      return;
    }
    if (tooLong) {
      setError("historyTooLong");
      return;
    }
    const task = scope();
    pending.current = task;
    const submitted = requestMessages.map(({ role, content }) => ({
      role,
      content,
    }));
    const body = {
      key_id: keyId,
      model: pair.model,
      provider: pair.provider,
      messages: submitted,
      max_tokens: maxResponse,
      save_history: saveHistory,
    };
    if (retryAsNew) clearOperationId(operationId);
    body.client_operation_id = reuseOperationId(
      operationId,
      JSON.stringify(body),
    );
    setRetryAsNew(false);
    setSending(true);
    setError("");
    setNotice("");
    try {
      const result = await playgroundCompletion(body, { signal: task.signal });
      if (!task.current()) return;
      if (result?.error || result?.status === "error")
        throw new Error("request_failed");
      const content = result?.choices?.[0]?.message?.content;
      // A settled retry may intentionally omit the original completion text.
      // Treat it as completed, not a malformed request to retry forever.
      if (
        result?._uwenzai?.idempotent_retry === true &&
        typeof content !== "string"
      ) {
        setMessages(
          saveHistory
            ? submitted
            : boundedDisplay([...messages, currentMessage]),
        );
        setPrompt("");
        clearOperationId(operationId);
        setNotice("completedReplay");
        onComplete(result);
        return;
      }
      if (typeof content !== "string" || content.length > MAX_PROMPT_CHARS)
        throw Object.assign(new Error("invalid_response"), {
          code: "invalid_response",
        });
      const reply = { role: "assistant", content };
      setMessages(
        saveHistory
          ? [...submitted, reply]
          : boundedDisplay([...messages, currentMessage, reply]),
      );
      setPrompt("");
      clearOperationId(operationId);
      if (
        saveHistory &&
        (result._uwenzai?.history_saved === false ||
          result._uwenzai?.history_warning)
      )
        setNotice("historySaveWarning");
      onComplete(result);
    } catch (err) {
      if (task.current()) {
        setRetryAsNew(err?.terminal === true && err?.refund_confirmed === true);
        setError(errorText(err, "sendFailed"));
      }
    } finally {
      if (task.current()) setSending(false);
      if (pending.current === task) pending.current = null;
      task.done();
    }
  }
  function stop() {
    if (!pending.current || saveHistory) return;
    pending.current.abort();
    pending.current = null;
    setSending(false);
    setNotice("stopped");
  }
  function updatePrompt(value) {
    if (value.length > MAX_PROMPT_CHARS) {
      setError("promptTooLong");
      return;
    }
    setPrompt(value);
    setError("");
  }
  return (
    <div className="dev-chat-body">
      <label className="dev-field dev-chat-key">
        <span>{tx("keySelect")}</span>
        <select
          value={keyId}
          onChange={(e) => setKeyId(e.target.value)}
          disabled={sending || !keysReady}
        >
          <option value="">{tx("selectKey")}</option>
          {activeKeys.map((key) => (
            <option key={key.id} value={String(key.id)}>
              {key.label || tx("unnamedKey")} · {key.hint}
            </option>
          ))}
        </select>
      </label>
      <div className="dev-history">
        <div className="dev-history-actions">
          <label>
            <input
              type="checkbox"
              aria-describedby="dev-history-notice"
              checked={saveHistory}
              disabled={!pair || sending || historyState === "deleting"}
              onChange={(e) =>
                e.target.checked ? loadHistory() : deleteHistory(true)
              }
            />
            {tx("history")}
          </label>
          <button
            type="button"
            className="dev-button dev-icon-button"
            aria-label={tx("deleteHistory")}
            title={tx("deleteHistory")}
            disabled={!pair || sending || historyState === "deleting"}
            onClick={() => deleteHistory(false)}
          >
            <Trash2 size={16} />
          </button>
        </div>
        <p className="dev-footnote">
          {tx(saveHistory ? "historyOn" : "historyOff")}
        </p>
        <p id="dev-history-notice" className="dev-footnote">
          {tx("historyNotice").slice(0, -`${tx("privacyPolicy")}.`.length)}
          <a
            className="dev-legal-link"
            href="/privacy"
            target="_blank"
            rel="noopener noreferrer"
          >
            {tx("privacyPolicy")}
          </a>
          .
        </p>
        {sending && saveHistory && (
          <p className="dev-footnote">{tx("pendingHistory")}</p>
        )}
        {busyHistory && (
          <p role="status" className="dev-footnote">
            {tx(historyState === "deleting" ? "deleting" : "historyLoading")}
          </p>
        )}
        <InlineError
          code={historyError}
          tx={tx}
          retry={
            historyState === "deleteFailed"
              ? () => deleteHistory(deleteToOff)
              : loadHistory
          }
        />
      </div>
      <div
        className="dev-message-log"
        role="log"
        aria-label={tx("chat")}
        ref={log}
        aria-live="polite"
      >
        {messages.length === 0 ? (
          <div className="dev-chat-empty">
            <Terminal size={28} strokeWidth={1.5} />
            <h3>{tx("emptyChat")}</h3>
            <p>{pair ? tx("emptyChatHint") : tx("pairRequired")}</p>
          </div>
        ) : (
          messages.map((message, index) => (
            <article
              className={`dev-message dev-message-${message.role}`}
              key={index}
            >
              <span>{tx(message.role)}</span>
              <p>{message.content}</p>
            </article>
          ))
        )}
        {sending && (
          <p className="dev-generating" role="status">
            <span />
            {tx("sending")}
          </p>
        )}
      </div>
      <form className="dev-composer" onSubmit={send}>
        <p className="dev-cost-notice">
          <Activity size={15} />
          {tx("billNotice")}
        </p>
        <InlineError code={error} tx={tx} />
        {notice && (
          <p className="dev-notice" role="status">
            {tx(notice)}
          </p>
        )}
        <label className="dev-field">
          <span>{tx("prompt")}</span>
          <textarea
            rows={3}
            value={prompt}
            onChange={(e) => updatePrompt(e.target.value)}
            onPaste={(e) => {
              const input = e.currentTarget;
              const newLength =
                prompt.length -
                (input.selectionEnd - input.selectionStart) +
                e.clipboardData.getData("text").length;
              if (newLength > MAX_PROMPT_CHARS) {
                e.preventDefault();
                setError("promptTooLong");
              }
            }}
            placeholder={tx("promptPlaceholder")}
            disabled={sending || !pair}
            aria-describedby="dev-token-estimate"
          />
        </label>
        <p className="dev-footnote" id="dev-token-estimate">
          {tx("estimate", {
            count: estimate.toLocaleString(),
            limit: limits.max_input_tokens.toLocaleString(),
          })}
        </p>
        {tooLong && prompt && (
          <p className="dev-error-text" role="alert">
            {tx(
              messageLimitError([currentMessage], limits)
                ? "promptTooLong"
                : "historyTooLong",
            )}
          </p>
        )}
        <div className="dev-composer-actions">
          <label className="dev-field dev-token-input">
            <span>{tx("maxTokens")}</span>
            <input
              type="number"
              min={1}
              max={limits.max_output_tokens}
              step={1}
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
              disabled={sending}
            />
          </label>
          {sending && !saveHistory ? (
            <button type="button" className="dev-button" onClick={stop}>
              <Square size={14} />
              {tx("stop")}
            </button>
          ) : (
            <button
              className="dev-button dev-primary"
              disabled={
                sending ||
                !pair ||
                !keyReady ||
                historyBlocked ||
                !prompt.trim() ||
                !!tooLong ||
                !maxValid
              }
            >
              <Send size={16} />
              {tx(sending ? "sending" : retryAsNew ? "newPaidAttempt" : "send")}
            </button>
          )}
        </div>
        <p className="dev-footnote dev-text-only">{tx("textOnly")}</p>
        {saveHistory && (
          <p className="dev-footnote">
            {tx("requestLimit", { count: limits.max_messages })}
          </p>
        )}
      </form>
    </div>
  );
}

function Usage({ tx, language, data, last, busy, error, refresh, enabled }) {
  const [period, setPeriod] = useState("month");
  const number = (value) =>
    value === undefined || value === null
      ? "—"
      : Number(value).toLocaleString(language === "sw" ? "sw-KE" : "en-KE", {
          maximumFractionDigits: 3,
        });
  const prefix = period === "month" ? "monthly" : "total";
  const rows = data?.by_model || [];
  const totals = data?.usage;
  const input = last?.usage?.prompt_tokens;
  const output = last?.usage?.completion_tokens;
  const tokenTotal = (Number(input) || 0) + (Number(output) || 0);
  return (
    <>
      <div className="dev-usage-heading">
        <div className="dev-section-heading">
          <Activity size={18} />
          <h2>{tx("usageTitle")}</h2>
        </div>
        <button
          type="button"
          className="dev-button"
          onClick={refresh}
          disabled={!enabled || busy}
        >
          <RotateCcw size={15} />
          {tx("refresh")}
        </button>
      </div>
      <InlineError code={error} tx={tx} retry={refresh} />
      <div className="dev-last-request">
        <div>
          <span className="dev-label">{tx("lastRequest")}</span>
          <p className="dev-footnote">
            {last ? `${last.model} / ${last.provider}` : tx("noLastRequest")}
          </p>
        </div>
        <div className="dev-meter-block">
          <div
            className="dev-meter"
            role="img"
            aria-label={`${tx("usageMeter")}: ${number(input)} / ${number(output)}`}
          >
            <span
              style={{
                width: tokenTotal
                  ? `${(Number(input) / tokenTotal) * 100}%`
                  : "0%",
              }}
            />
            <span
              style={{
                width: tokenTotal
                  ? `${(Number(output) / tokenTotal) * 100}%`
                  : "0%",
              }}
            />
          </div>
          <p className="dev-footnote">
            {tx("input")} <strong>{number(input)}</strong> <span> / </span>
            {tx("output")} <strong>{number(output)}</strong>
          </p>
        </div>
        <div className="dev-last-sparks">
          <strong>
            {number(
              last?._uwenzai?.original_sparks_charged ??
                last?._uwenzai?.sparks_charged,
            )}
          </strong>
          <span className="dev-footnote">
            {tx("sparks")} · {tx("billed")}
          </span>
        </div>
      </div>
      <div className="dev-usage-period">
        <p className="dev-muted">{tx("allPairs")}</p>
        <div className="dev-segmented">
          {["month", "allTime"].map((name) => (
            <button
              type="button"
              key={name}
              aria-pressed={period === name}
              onClick={() => setPeriod(name)}
            >
              {tx(name)}
            </button>
          ))}
        </div>
      </div>
      {busy && !data ? (
        <p role="status" className="dev-muted">
          {tx("loading")}
        </p>
      ) : (
        <>
          <p className="dev-footnote dev-table-hint">{tx("scrollColumns")}</p>
          <div className="dev-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>
                    {tx("model")} / {tx("provider")}
                  </th>
                  <th>{tx("input")}</th>
                  <th>{tx("output")}</th>
                  <th>{tx("calls")}</th>
                  <th>{tx("sparks")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.model}/${row.provider}`}>
                    <td>
                      <strong>{row.model}</strong>
                      <span>{row.provider}</span>
                    </td>
                    <td>{number(row[`${prefix}_input_tokens`])}</td>
                    <td>{number(row[`${prefix}_output_tokens`])}</td>
                    <td>{number(row[`${prefix}_calls`])}</td>
                    <td>{number(row[`${prefix}_sparks`])}</td>
                  </tr>
                ))}
                {totals && (
                  <tr className="dev-totals">
                    <td>{tx("total")}</td>
                    <td>
                      {number(
                        totals[`${prefix}_input_tokens`] ??
                          (period === "month"
                            ? rows.reduce(
                                (n, row) =>
                                  n + Number(row.monthly_input_tokens || 0),
                                0,
                              )
                            : undefined),
                      )}
                    </td>
                    <td>
                      {number(
                        totals[`${prefix}_output_tokens`] ??
                          (period === "month"
                            ? rows.reduce(
                                (n, row) =>
                                  n + Number(row.monthly_output_tokens || 0),
                                0,
                              )
                            : undefined),
                      )}
                    </td>
                    <td>{number(totals[`${prefix}_calls`])}</td>
                    <td>{number(totals[`${prefix}_sparks`])}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {rows.length === 0 && <p className="dev-footnote">{tx("noUsage")}</p>}
        </>
      )}
    </>
  );
}
