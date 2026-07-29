import { Link, useLocation, useNavigate } from "react-router-dom";
import { useState } from "react";
import { KeyRound, LockKeyhole, Mail, ShieldCheck } from "lucide-react";
import PrimaryButton from "../components/PrimaryButton";
import MessagePopup from "../components/MessagePopup";
import { useLanguage } from "../context/LanguageContext";
import { getAuthMessage, getAuthTitle } from "../utils/authMessageMapper";
import {
  requestPasswordReset,
  confirmPasswordReset,
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

function safeT(t, key, fallback) {
  const value = t(key);
  return value && value !== key ? value : fallback;
}

function maskEmail(email) {
  if (!email || !email.includes("@")) return "";
  const [local, domain] = email.split("@");
  if (!local) return email;
  if (local.length <= 2) return `${local[0] || ""}***@${domain}`;
  return `${local.slice(0, 2)}***@${domain}`;
}

export default function PasswordRecovery() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();

  const state = location.state || {};
  const initialEmail = state.email || "";

  const [step, setStep] = useState("request");
  const [formData, setFormData] = useState({
    email: initialEmail,
    code: "",
    password: "",
    confirm_password: "",
  });

  const [errors, setErrors] = useState({});
  const [requesting, setRequesting] = useState(false);
  const [resetting, setResetting] = useState(false);

  const [popup, setPopup] = useState({
    open: false,
    type: "info",
    title: "",
    message: "",
  });

  const pageTitle = safeT(t, "auth.passwordRecovery", "Password recovery");
  const requestSubtitle = safeT(
    t,
    "auth.passwordRecoverySubtitle",
    "Enter your email address and we’ll send you a recovery code.",
  );
  const resetSubtitle = safeT(
    t,
    "auth.resetPasswordSubtitle",
    "Enter the recovery code you received and choose a new password.",
  );

  const emailLabel = safeT(t, "auth.email", "Email");
  const codeLabel = safeT(t, "auth.recoveryCode", "Recovery Code");
  const passwordLabel = safeT(t, "auth.newPassword", "New Password");
  const confirmPasswordLabel = safeT(
    t,
    "auth.confirmNewPassword",
    "Confirm New Password",
  );

  const sendCodeLabel = safeT(t, "auth.sendRecoveryCode", "Send recovery code");
  const resetPasswordLabel = safeT(t, "auth.resetPassword", "Reset password");
  const loginLabel = safeT(t, "common.login", "Login");

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

  const validateRequest = () => {
    const nextErrors = {};

    if (!formData.email.trim()) {
      nextErrors.email = "Please enter your email address.";
    } else if (!/\S+@\S+\.\S+/.test(formData.email.trim())) {
      nextErrors.email = "Please enter a valid email address.";
    }

    return nextErrors;
  };

  const validateReset = () => {
    const nextErrors = {};

    if (!formData.email.trim()) {
      nextErrors.email = "Please enter your email address.";
    } else if (!/\S+@\S+\.\S+/.test(formData.email.trim())) {
      nextErrors.email = "Please enter a valid email address.";
    }

    if (!formData.code.trim()) {
      nextErrors.code = "Please enter the recovery code.";
    } else if (!/^\d{6}$/.test(formData.code.trim())) {
      nextErrors.code = "Enter the 6-digit recovery code.";
    }

    if (!formData.password) {
      nextErrors.password = "Please enter your new password.";
    } else if (formData.password.length < 6) {
      nextErrors.password = "Your password must be at least 6 characters long.";
    } else if (formData.password.length > 20) {
      nextErrors.password = "Your password must not exceed 20 characters.";
    }

    if (!formData.confirm_password) {
      nextErrors.confirm_password = "Please confirm your new password.";
    } else if (formData.password !== formData.confirm_password) {
      nextErrors.confirm_password = "Your passwords do not match.";
    }

    return nextErrors;
  };

  const handleChange = (event) => {
    const { name, value } = event.target;

    let nextValue = value;

    if (name === "code") {
      nextValue = value.replace(/\D/g, "").slice(0, 6);
    }

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

  const handleRequestCode = async (event) => {
    event.preventDefault();

    const nextErrors = validateRequest();
    setErrors(nextErrors);
    closePopup();

    if (Object.keys(nextErrors).length > 0) return;

    setRequesting(true);

    try {
      const result = await requestPasswordReset({
        email: formData.email.trim(),
      });

      showPopup({
        type: "success",
        title: getAuthTitle(result?.data?.message, "Recovery email sent"),
        message: getAuthMessage(
          result?.data?.message,
          "If an account exists for that email, a recovery code has been sent.",
        ),
      });

      setStep("reset");
    } catch (err) {
      showPopup({
        type: "error",
        title: getAuthTitle(err, "Could not send recovery code"),
        message: getAuthMessage(
          err,
          "We could not send a recovery code right now. Please try again.",
        ),
      });
    } finally {
      setRequesting(false);
    }
  };

  const handleResetPassword = async (event) => {
    event.preventDefault();

    const nextErrors = validateReset();
    setErrors(nextErrors);
    closePopup();

    if (Object.keys(nextErrors).length > 0) return;

    setResetting(true);

    try {
      const result = await confirmPasswordReset({
        email: formData.email.trim(),
        code: formData.code.trim(),
        password: formData.password,
        confirm_password: formData.confirm_password,
      });

      showPopup({
        type: "success",
        title: getAuthTitle(result?.data?.message, "Password reset successful"),
        message: getAuthMessage(
          result?.data?.message,
          "Your password has been reset successfully.",
        ),
      });

      setTimeout(() => {
        navigate("/login", {
          state: { email: formData.email.trim() },
        });
      }, 1200);
    } catch (err) {
      showPopup({
        type: "error",
        title: getAuthTitle(err, "Password reset failed"),
        message: getAuthMessage(
          err,
          "We could not reset your password right now. Please try again.",
        ),
      });
    } finally {
      setResetting(false);
    }
  };

  const backToRequestStep = () => {
    setErrors({});
    closePopup();
    setStep("request");
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
        <h2 className="text-2xl font-semibold theme-text">{pageTitle}</h2>
        <p className="mt-2 text-sm leading-6 theme-text-muted">
          {step === "request" ? requestSubtitle : resetSubtitle}
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
            <p className="text-sm font-medium theme-text">Email address</p>
            <p className="mt-1 text-sm theme-text-muted">
              {formData.email
                ? maskEmail(formData.email)
                : "Use the email address attached to your account."}
            </p>
          </div>
        </div>
      </div>

      {step === "request" ? (
        <form className="space-y-5" onSubmit={handleRequestCode}>
          <Input
            name="email"
            label={emailLabel}
            type="email"
            value={formData.email}
            onChange={handleChange}
            autoComplete="email"
            icon={Mail}
            error={errors.email}
            hint="We’ll send a 6-digit recovery code to this email."
          />

          <div className="grid grid-cols-1 gap-3 pt-1">
            <PrimaryButton fullWidth type="submit" disabled={requesting}>
              {requesting ? "Sending..." : sendCodeLabel}
            </PrimaryButton>

            <Link to="/login" className="block">
              <PrimaryButton
                variant="secondary"
                fullWidth
                disabled={requesting}
              >
                {loginLabel}
              </PrimaryButton>
            </Link>
          </div>
        </form>
      ) : (
        <form className="space-y-5" onSubmit={handleResetPassword}>
          <Input
            name="email"
            label={emailLabel}
            type="email"
            value={formData.email}
            onChange={handleChange}
            autoComplete="email"
            icon={Mail}
            error={errors.email}
          />

          <Input
            name="code"
            label={codeLabel}
            type="text"
            value={formData.code}
            onChange={handleChange}
            autoComplete="one-time-code"
            icon={ShieldCheck}
            error={errors.code}
            hint="Enter the 6-digit code sent to your email."
            maxLength={6}
          />

          <Input
            name="password"
            label={passwordLabel}
            type="password"
            value={formData.password}
            onChange={handleChange}
            autoComplete="new-password"
            icon={KeyRound}
            error={errors.password}
            hint="Use 6 to 20 characters."
          />

          <Input
            name="confirm_password"
            label={confirmPasswordLabel}
            type="password"
            value={formData.confirm_password}
            onChange={handleChange}
            autoComplete="new-password"
            icon={LockKeyhole}
            error={errors.confirm_password}
          />

          <div className="grid grid-cols-1 gap-3 pt-1 sm:grid-cols-2">
            <PrimaryButton
              fullWidth
              type="submit"
              disabled={resetting || requesting}
            >
              {resetting ? "Resetting..." : resetPasswordLabel}
            </PrimaryButton>

            <PrimaryButton
              type="button"
              variant="secondary"
              fullWidth
              disabled={resetting || requesting}
              onClick={backToRequestStep}
            >
              Send new code
            </PrimaryButton>
          </div>

          <Link to="/login" className="block">
            <PrimaryButton
              variant="secondary"
              fullWidth
              disabled={resetting || requesting}
            >
              {loginLabel}
            </PrimaryButton>
          </Link>
        </form>
      )}
    </div>
  );
}
