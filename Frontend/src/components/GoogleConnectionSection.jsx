import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import { getAuthSessionGeneration } from "../services/apiClient";
import { linkGoogleAccount } from "../services/authService";
import { getAuthMessage } from "../utils/authMessageMapper";
import GoogleSignInButton from "./GoogleSignInButton";
import PrimaryButton from "./PrimaryButton";

// This decoding is ONLY a display/mistake-prevention hint. The backend verifies
// the signature, audience, expiry, email and matching authenticated account.
function readSelection(credential) {
  try {
    const encoded = credential
      .split(".")[1]
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
    const claims = JSON.parse(new TextDecoder().decode(bytes));
    if (typeof claims.email !== "string" || !Number.isFinite(claims.exp))
      return null;
    return {
      email: claims.email.trim().toLowerCase(),
      expiresAt: claims.exp * 1000,
    };
  } catch {
    return null;
  }
}

export default function GoogleConnectionSection() {
  const { user, isAuthenticated, sessionGeneration } = useAuth();
  if (!isAuthenticated) return null;
  return (
    <ScopedGoogleConnection
      key={`${user?.user_id}:${sessionGeneration}:${user?.email}`}
    />
  );
}

function ScopedGoogleConnection() {
  const { user, clearAuthState } = useAuth();
  const navigate = useNavigate();
  const { t, language } = useLanguage();
  const pending = useRef(null);
  const controller = useRef(null);
  const generation = useRef(getAuthSessionGeneration());
  const [selection, setSelection] = useState(null);
  const [choosing, setChoosing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [needsReauth, setNeedsReauth] = useState(false);
  const email = user?.email?.trim().toLowerCase();
  const current = () =>
    controller.current &&
    !controller.current.signal.aborted &&
    generation.current === getAuthSessionGeneration();

  useEffect(() => {
    controller.current = new AbortController();
    return () => {
      controller.current.abort();
      pending.current = null;
    };
  }, []);

  const cancel = () => {
    // A dispatched confirmation may already have reached the server. Abort
    // retires UI work; it cannot promise to undo a confirmed server mutation.
    controller.current.abort();
    controller.current = new AbortController();
    pending.current = null;
    setSelection(null);
    setChoosing(false);
    setSaving(false);
    setError("");
  };

  const select = (credential) => {
    if (!current()) return;
    const info = readSelection(credential);
    setChoosing(false);
    if (!info || info.expiresAt <= Date.now()) {
      setError(t("settings.googleExpired"));
      return;
    }
    if (!email || info.email !== email) {
      setError(t("settings.googleWrongAccount"));
      return;
    }
    pending.current = credential;
    setSelection(info);
  };

  const confirm = async () => {
    if (!current() || saving || !pending.current || !selection) return;
    if (selection.expiresAt <= Date.now()) {
      cancel();
      setError(t("settings.googleExpired"));
      return;
    }
    const attempt = controller.current;
    const credential = pending.current;
    pending.current = null;
    setSaving(true);
    setError("");
    try {
      await linkGoogleAccount(credential, {
        expectedUserId: user.user_id,
        signal: attempt.signal,
      });
      if (!current() || attempt.signal.aborted) return;
      setConnected(true);
    } catch (err) {
      if (!current() || attempt.signal.aborted) return;
      if (err.status === 401 || err.code === "token_not_valid") {
        setNeedsReauth(true);
      } else {
        setError(
          getAuthMessage(err, t("settings.googleConnectError"), language),
        );
      }
    } finally {
      if (current() && !attempt.signal.aborted) {
        setSelection(null);
        setSaving(false);
      }
    }
  };

  return (
    <div
      id="google-connection"
      className="rounded-2xl border p-4 theme-card space-y-3"
    >
      <h3 className="text-sm font-semibold theme-text">
        {t("settings.googleConnect")}
      </h3>
      <p className="text-sm leading-6 theme-text-muted">
        {t("settings.googleConnectDescription")}
      </p>
      <p className="text-sm theme-text break-words">
        {t("settings.googleSignedInAs")} <strong>{user?.username}</strong> (
        {email})
      </p>
      {connected ? (
        <p role="status" className="text-sm theme-text">
          {t("settings.googleConnected")}
        </p>
      ) : needsReauth ? (
        <div className="space-y-3">
          <p role="alert" className="text-sm leading-6 theme-text-muted">
            {t("settings.googleReauthRequired")}
          </p>
          <PrimaryButton
            type="button"
            onClick={() => {
              if (!current()) return;
              cancel();
              clearAuthState();
              navigate("/login", {
                replace: true,
                state: { connectGoogle: true },
              });
            }}
          >
            {t("settings.googleReauth")}
          </PrimaryButton>
        </div>
      ) : selection ? (
        <div className="space-y-3">
          <p className="text-sm leading-6 theme-text break-words">
            {t("settings.googleConfirmDescription")}{" "}
            <strong>{selection.email}</strong>
          </p>
          <div className="flex flex-wrap gap-2">
            <PrimaryButton type="button" onClick={confirm} disabled={saving}>
              {t(
                saving ? "settings.googleConnecting" : "settings.googleConfirm",
              )}
            </PrimaryButton>
            <PrimaryButton
              type="button"
              variant="secondary"
              onClick={cancel}
              disabled={saving}
            >
              {t("common.cancel")}
            </PrimaryButton>
          </div>
        </div>
      ) : choosing ? (
        <div className="space-y-3">
          <GoogleSignInButton
            onCredential={select}
            onError={(err) => {
              setChoosing(false);
              setError(
                getAuthMessage(err, t("settings.googleConnectError"), language),
              );
            }}
          />
          <PrimaryButton type="button" variant="secondary" onClick={cancel}>
            {t("common.cancel")}
          </PrimaryButton>
        </div>
      ) : (
        <PrimaryButton
          type="button"
          disabled={!email}
          onClick={() => {
            setError("");
            setChoosing(true);
          }}
        >
          {t("settings.googleChoose")}
        </PrimaryButton>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-500">
          {error}
        </p>
      )}
    </div>
  );
}
