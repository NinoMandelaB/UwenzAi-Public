import { Link, useLocation, useNavigate } from "react-router-dom";
import { useState } from "react";
import { Mail, RotateCcw, ShieldCheck } from "lucide-react";
import PrimaryButton from "../components/PrimaryButton";
import MessagePopup from "../components/MessagePopup";
import { useLanguage } from "../context/LanguageContext";
import { getAuthMessage, getAuthTitle } from "../utils/authMessageMapper";
import {
  verifyEmailCode,
  resendVerificationCode,
} from "../services/authService";

function Input({
  label,
  type = "text",
  value,
  onChange,
  name,
  autoComplete,
  icon,
  error,
  hint,
  maxLength,
}) {
  const Icon = icon;
  const [isFocused, setIsFocused] = useState(false);
  const isFloating = isFocused || (value && value.length > 0);

  return (
    <div className="relative pt-2">
      <div
        className="group relative flex items-center gap-3 rounded-2xl border px-4 py-4 transition-all duration-200"
        style={{
          borderColor: error
            ? "rgba(239, 68, 68, 0.6)"
            : isFocused
              ? "var(--color-accent)"
              : "var(--color-border)",
          backgroundColor: "var(--color-input)",
        }}
      >
        <label
          className={`pointer-events-none absolute transition-all duration-200 ${
            isFloating
              ? "-top-2.5 left-4 px-2 text-xs font-bold uppercase tracking-wider"
              : "left-11 text-sm"
          }`}
          style={{
            color: error
              ? "rgb(220, 38, 38)"
              : isFloating
                ? "var(--color-accent)"
                : "var(--color-text-muted)",
            backgroundColor: isFloating ? "var(--color-shell)" : "transparent",
            zIndex: 10,
          }}
        >
          {label}
        </label>

        {Icon && (
          <Icon
            size={18}
            className="transition-colors duration-200"
            style={{
              color: error
                ? "rgb(220, 38, 38)"
                : isFocused
                  ? "var(--color-accent)"
                  : "var(--color-text-muted)",
              flexShrink: 0,
            }}
          />
        )}

        <input
          name={name}
          type={type}
          value={value}
          onChange={onChange}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          autoComplete={autoComplete}
          maxLength={maxLength}
          className="w-full bg-transparent text-sm outline-none"
          style={{ color: "var(--color-text)" }}
        />
      </div>

      {error ? (
        <p
          className="ml-4 mt-1.5 text-[11px] font-medium"
          style={{ color: "rgb(220, 38, 38)" }}
        >
          {error}
        </p>
      ) : hint ? (
        <p className="ml-4 mt-1.5 text-[11px] theme-text-muted">{hint}</p>
      ) : null}
    </div>
  );
}

function maskEmail(email) {
  if (!email || !email.includes("@")) return "";
  const [local, domain] = email.split("@");
  if (!local) return email;
  if (local.length <= 2) return `${local[0] || ""}***@${domain}`;
  return `${local.slice(0, 2)}***@${domain}`;
}

export default function VerifyEmail() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();

  const state = location.state || {};
  const initialUserId = state.user_id || "";
  const initialEmail = state.email || "";

  const [formData, setFormData] = useState({
    user_id: initialUserId,
    email: initialEmail,
    code: "",
  });

  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);

  const [popup, setPopup] = useState({
    open: false,
    type: "info",
    title: "",
    message: "",
  });

  const showPopup = ({ type = "info", title = "", message = "" }) => {
    setPopup({
      open: true,
      type,
      title,
      message,
    });
  };

  const closePopup = () => {
    setPopup((prev) => ({
      ...prev,
      open: false,
    }));
  };

  const validateVerify = () => {
    const nextErrors = {};

    if (!formData.user_id.trim()) {
      nextErrors.user_id = t("auth.missingVerificationReference");
    }

    if (!formData.code.trim()) {
      nextErrors.code = t("auth.verificationCodeRequired");
    } else if (!/^\d{6}$/.test(formData.code.trim())) {
      nextErrors.code = t("auth.verificationCodeInvalid");
    }

    return nextErrors;
  };

  const validateResend = () => {
    const nextErrors = {};

    if (!formData.user_id.trim()) {
      nextErrors.user_id = t("auth.missingVerificationReference");
    }

    return nextErrors;
  };

  const handleChange = (event) => {
    const { name, value } = event.target;

    const nextValue =
      name === "code" ? value.replace(/\D/g, "").slice(0, 6) : value;

    setFormData((prev) => ({
      ...prev,
      [name]: nextValue,
    }));

    setErrors((prev) => ({
      ...prev,
      [name]: "",
    }));

    closePopup();
  };

  const handleSubmit = async (event) => {
    event.preventDefault();

    const nextErrors = validateVerify();
    setErrors(nextErrors);
    closePopup();

    if (Object.keys(nextErrors).length > 0) return;

    setLoading(true);

    try {
      const result = await verifyEmailCode({
        user_id: formData.user_id.trim(),
        code: formData.code.trim(),
      });

      showPopup({
        type: "success",
        title: getAuthTitle(
          result?.data?.message,
          t("auth.emailVerifiedTitle"),
        ),
        message: getAuthMessage(
          result?.data?.message,
          t("auth.emailVerifiedMessage"),
        ),
      });

      setTimeout(() => {
        navigate("/login");
      }, 1200);
    } catch (err) {
      showPopup({
        type: "error",
        title: getAuthTitle(err, t("auth.verificationFailedTitle")),
        message: getAuthMessage(err, t("auth.verificationFailedMessage")),
      });
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    const nextErrors = validateResend();
    setErrors((prev) => ({ ...prev, ...nextErrors }));
    closePopup();

    if (Object.keys(nextErrors).length > 0) return;

    setResending(true);

    try {
      const result = await resendVerificationCode({
        user_id: formData.user_id.trim(),
      });

      showPopup({
        type: "success",
        title: getAuthTitle(result?.data?.message, t("auth.codeSentTitle")),
        message: getAuthMessage(
          result?.data?.message,
          t("auth.codeSentMessage"),
        ),
      });
    } catch (err) {
      showPopup({
        type: "error",
        title: getAuthTitle(err, t("auth.couldNotResendTitle")),
        message: getAuthMessage(err, t("auth.couldNotResendMessage")),
      });
    } finally {
      setResending(false);
    }
  };

  return (
    <div className="mx-auto max-w-md">
      <MessagePopup
        open={popup.open}
        type={popup.type}
        title={popup.title}
        message={popup.message}
        onClose={closePopup}
      />

      <div className="mb-8">
        <h2 className="text-2xl font-semibold theme-text">
          {t("auth.verifyEmail")}
        </h2>
        <p className="mt-2 text-sm leading-6 theme-text-muted">
          {t("auth.verifyEmailSubtitle")}
        </p>
      </div>

      <div
        className="mb-5 rounded-2xl border p-4"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-soft)",
        }}
      >
        <div className="flex items-start gap-3">
          <Mail
            size={18}
            style={{
              color: "var(--color-accent)",
              flexShrink: 0,
              marginTop: 2,
            }}
          />
          <div>
            <p className="text-sm font-medium theme-text">
              {t("auth.emailAddress")}
            </p>
            <p className="mt-1 text-sm theme-text-muted">
              {maskEmail(formData.email) || t("auth.useCodeSent")}
            </p>
          </div>
        </div>
      </div>

      <form className="space-y-5" onSubmit={handleSubmit}>
        <input type="hidden" name="user_id" value={formData.user_id} readOnly />

        <Input
          name="email"
          label={t("auth.email")}
          type="email"
          value={formData.email}
          onChange={handleChange}
          autoComplete="email"
          icon={Mail}
          error={errors.email}
        />

        <Input
          name="code"
          label={t("auth.verificationCode")}
          type="text"
          value={formData.code}
          onChange={handleChange}
          autoComplete="one-time-code"
          icon={ShieldCheck}
          error={errors.code || errors.user_id}
          hint={t("auth.verificationCodeHint")}
          maxLength={6}
        />

        <div className="grid grid-cols-1 gap-3 pt-1 sm:grid-cols-2">
          <PrimaryButton
            fullWidth
            type="submit"
            disabled={loading || resending}
          >
            {loading ? t("auth.verifying") : t("auth.verifyEmail")}
          </PrimaryButton>

          <PrimaryButton
            type="button"
            variant="secondary"
            fullWidth
            disabled={loading || resending}
            onClick={handleResend}
          >
            <span className="inline-flex items-center gap-2">
              <RotateCcw size={16} />
              {resending ? t("auth.sending") : t("auth.resendCode")}
            </span>
          </PrimaryButton>
        </div>

        <Link to="/login" className="block">
          <PrimaryButton
            variant="secondary"
            fullWidth
            disabled={loading || resending}
          >
            {t("common.login")}
          </PrimaryButton>
        </Link>
      </form>
    </div>
  );
}
