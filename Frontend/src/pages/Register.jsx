// src/pages/Register.jsx

import { Link, useNavigate } from "react-router-dom";
import { useRef, useState } from "react";
import ReCAPTCHA from "react-google-recaptcha";
import { Lock, User, Mail, Eye, EyeOff, Mars, Venus } from "lucide-react";
import PrimaryButton from "../components/PrimaryButton";
import MessagePopup from "../components/MessagePopup";
import { useLanguage } from "../context/LanguageContext";
import { signupUser } from "../services/authService";
import { useTheme } from "../context/ThemeContext";
import { getAuthMessage, getAuthTitle } from "../utils/authMessageMapper";
import { characterCount } from "../utils/textLimits";
import { LEGAL_POLICY_VERSION } from "../i18n/legal";

function Input({
  label,
  type = "text",
  value,
  onChange,
  name,
  autoComplete,
  icon,
  error,
}) {
  const Icon = icon;
  const [isFocused, setIsFocused] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const isFloating = isFocused || (value && value.length > 0);

  const isPassword = type === "password";
  const inputType = isPassword && showPassword ? "text" : type;

  const togglePasswordVisibility = () => {
    setShowPassword((prev) => !prev);
  };

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
          type={inputType}
          value={value}
          onChange={onChange}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          autoComplete={autoComplete}
          className="w-full bg-transparent text-sm outline-none"
          style={{ color: "var(--color-text)" }}
        />

        {isPassword && (
          <button
            type="button"
            onClick={togglePasswordVisibility}
            className="flex items-center justify-center focus:outline-none"
            aria-label={showPassword ? "Hide password" : "Show password"}
            style={{ color: "var(--color-text-muted)" }}
          >
            {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        )}
      </div>

      {error && (
        <p
          className="ml-4 mt-1.5 text-[11px] font-medium"
          style={{ color: "rgb(220, 38, 38)" }}
        >
          {error}
        </p>
      )}
    </div>
  );
}

// Gender toggle button group – no label, just two clickable buttons
function GenderToggle({ value, onChange, name, error }) {
  const options = [
    { value: "male", label: "Male", icon: Mars },
    { value: "female", label: "Female", icon: Venus },
  ];

  return (
    <div className="pt-2">
      <div className="flex gap-2 w-full">
        {options.map((option) => {
          const isSelected = value === option.value;
          const Icon = option.icon;
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                onChange({ target: { name, value: option.value } });
              }}
              className={`flex-1 flex items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-all duration-200 ${
                isSelected
                  ? "bg-[var(--color-accent)] text-[#082f49] shadow-sm"
                  : "bg-[var(--color-soft)] text-[var(--color-text-muted)] hover:opacity-80"
              }`}
              style={{
                border: isSelected
                  ? "1px solid var(--color-accent)"
                  : "1px solid transparent",
              }}
            >
              <Icon size={16} className="shrink-0" />
              {option.label}
            </button>
          );
        })}
      </div>
      {error && (
        <p
          className="ml-4 mt-1.5 text-[11px] font-medium"
          style={{ color: "rgb(220, 38, 38)" }}
        >
          {error}
        </p>
      )}
    </div>
  );
}

function safeT(t, key, fallback) {
  const value = t(key);
  return value && value !== key ? value : fallback;
}

export default function Register() {
  const { t, language } = useLanguage();
  const navigate = useNavigate();
  const { isDark } = useTheme();

  const recaptchaRef = useRef(null);
  const recaptchaSiteKey = import.meta.env.VITE_RECAPTCHA_SITE_KEY || "";

  const [formData, setFormData] = useState({
    username: "",
    email: "",
    password: "",
    confirmPassword: "",
    display_name: "",
    full_name: "",
    gender: "", // new gender field
  });

  const [captchaToken, setCaptchaToken] = useState("");
  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(false);

  const [popup, setPopup] = useState({
    open: false,
    type: "info",
    title: "",
    message: "",
  });

  // Compliance confirmations
  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [privacyAccepted, setPrivacyAccepted] = useState(false);

  const usernameLabel = safeT(t, "auth.username", "Username");
  const passwordLabel = safeT(t, "auth.password", "Password");
  const confirmPasswordLabel = safeT(
    t,
    "auth.confirmPassword",
    "Confirm Password",
  );
  const emailLabel = safeT(t, "auth.email", "Email");
  const displayNameLabel = safeT(t, "auth.nickname", "Screen Name");
  const createAccountLabel = safeT(t, "auth.createAccount", "Create account");
  const createAccountSubtitle = safeT(
    t,
    "auth.createAccountSubtitle",
    "Set up your profile to save chats, sync progress, and unlock more features.",
  );
  const alreadyHaveAccountLabel = safeT(
    t,
    "auth.alreadyHaveAccount",
    "Already have an account?",
  );
  const loginLabel = safeT(t, "common.login", "Login");
  const guestLabel = safeT(t, "common.continueAsGuest", "Continue as Guest");
  const signupLabel = safeT(t, "common.signup", "Sign Up");

  const showPopup = ({ type = "info", title = "", message = "" }) => {
    setPopup({ open: true, type, title, message });
  };

  const closePopup = () => {
    setPopup((prev) => ({ ...prev, open: false }));
  };

  const resetRecaptcha = () => {
    recaptchaRef.current?.reset();
    setCaptchaToken("");
  };

  const validate = () => {
    const nextErrors = {};
    const username = formData.username.trim().toLowerCase();
    const email = formData.email.trim();
    const password = formData.password;
    const confirmPassword = formData.confirmPassword;
    const displayName = formData.display_name.trim();

    if (!username)
      nextErrors.username = t("auth.usernameRequired", "Username is required.");
    if (!email)
      nextErrors.email = t("auth.emailRequired", "Email is required.");
    if (!password)
      nextErrors.password = t("auth.passwordRequired", "Password is required.");
    if (characterCount(password) < 8)
      nextErrors.password = t(
        "auth.passwordMinLength",
        "Password must be at least 8 characters.",
      );
    if (characterCount(password) > 128)
      nextErrors.password = t("auth.passwordMaxLength");
    if (password !== confirmPassword)
      nextErrors.confirmPassword = t(
        "auth.passwordMismatch",
        "Passwords do not match.",
      );
    if (!displayName)
      nextErrors.display_name = t(
        "auth.displayNameRequired",
        "Screen name is required.",
      );

    if (!formData.gender) {
      nextErrors.gender = t(
        "auth.genderRequired",
        "Please select your gender.",
      );
    }

    if (!ageConfirmed)
      nextErrors.ageConfirmed = t(
        "auth.ageConfirmationRequired",
        "You must confirm you are 18 or older.",
      );
    if (!termsAccepted)
      nextErrors.termsAccepted = t(
        "auth.termsRequired",
        "You must accept the Terms of Service.",
      );
    if (!privacyAccepted)
      nextErrors.privacyAccepted = t(
        "auth.privacyRequired",
        "You must accept the Privacy Policy.",
      );

    if (!recaptchaSiteKey) {
      nextErrors.recaptcha = t(
        "auth.recaptchaNotConfigured",
        "reCAPTCHA is not configured. Please try again later.",
      );
    } else if (!captchaToken) {
      nextErrors.recaptcha = t(
        "auth.recaptchaRequired",
        "Please confirm that you are not a robot.",
      );
    }

    return nextErrors;
  };

  const handleChange = (event) => {
    const { name, value } = event.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    setErrors((prev) => ({ ...prev, [name]: "" }));
    closePopup();
  };

  const handleCaptchaChange = (token) => {
    setCaptchaToken(token || "");
    setErrors((prev) => ({ ...prev, recaptcha: "" }));
    closePopup();
  };

  const handleCaptchaExpired = () => {
    setCaptchaToken("");
    setErrors((prev) => ({
      ...prev,
      recaptcha: "Verification expired. Please try again.",
    }));
  };

  const handleCaptchaErrored = () => {
    setCaptchaToken("");
    showPopup({
      type: "error",
      title: "Verification failed",
      message: "The not-a-robot check could not be loaded. Please try again.",
    });
  };

  const handleSubmit = async (event) => {
    event.preventDefault();

    const nextErrors = validate();
    setErrors(nextErrors);
    closePopup();

    if (Object.keys(nextErrors).length > 0) return;

    setLoading(true);

    const userTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

    try {
      const result = await signupUser({
        username: formData.username.trim().toLowerCase(),
        email: formData.email.trim(),
        password: formData.password,
        confirm_password: formData.confirmPassword,
        full_name: formData.full_name.trim() || formData.display_name.trim(),
        display_name: formData.display_name.trim(),
        gender: formData.gender || undefined,
        recaptcha_token: captchaToken,
        age_confirmed: ageConfirmed,
        terms_accepted: termsAccepted,
        privacy_accepted: privacyAccepted,
        compliance_version: LEGAL_POLICY_VERSION,
        timezone: userTimeZone,
      });

      const userId = result?.data?.user?.user_id || "";
      const userEmail = formData.email.trim();

      navigate("/verify-email", {
        state: { user_id: userId, email: userEmail },
      });
    } catch (err) {
      resetRecaptcha();

      let errorTitle = getAuthTitle(err, "Signup failed");
      let errorMessage = getAuthMessage(
        err,
        "Unable to create account right now. Please try again.",
        language,
      );

      if (err.status === 429 || err.code === "rate_limited") {
        errorTitle = "Too many requests";
        errorMessage =
          err.message ||
          "You have made too many signup attempts. Please wait a few minutes before trying again.";
      }

      showPopup({ type: "error", title: errorTitle, message: errorMessage });
    } finally {
      setLoading(false);
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
          {createAccountLabel}
        </h2>
        <p className="mt-2 text-sm leading-6 theme-text-muted">
          {createAccountSubtitle}
        </p>
      </div>

      <form className="space-y-5" onSubmit={handleSubmit}>
        <Input
          name="username"
          label={usernameLabel}
          type="text"
          value={formData.username}
          onChange={handleChange}
          autoComplete="username"
          icon={User}
          error={errors.username}
        />

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
          name="display_name"
          label={displayNameLabel}
          type="text"
          value={formData.display_name}
          onChange={handleChange}
          autoComplete="nickname"
          icon={User}
          error={errors.display_name}
        />

        {/* Gender toggle – replaces phone number */}
        <GenderToggle
          name="gender"
          value={formData.gender}
          onChange={handleChange}
          error={errors.gender}
        />

        <Input
          name="password"
          label={passwordLabel}
          type="password"
          value={formData.password}
          onChange={handleChange}
          autoComplete="new-password"
          icon={Lock}
          error={errors.password}
        />

        <Input
          name="confirmPassword"
          label={confirmPasswordLabel}
          type="password"
          value={formData.confirmPassword}
          onChange={handleChange}
          autoComplete="new-password"
          icon={Lock}
          error={errors.confirmPassword}
        />

        {/* Compliance Confirmations */}
        <div className="space-y-2">
          <label className="flex items-start gap-2 text-sm theme-text">
            <input
              type="checkbox"
              checked={ageConfirmed}
              onChange={(e) => {
                setAgeConfirmed(e.target.checked);
                setErrors((prev) => ({ ...prev, ageConfirmed: "" }));
              }}
              className="mt-1 rounded border-gray-400 accent-[var(--color-accent)]"
            />
            <span>
              I confirm that I am 18 years of age or older.{" "}
              <span className="text-red-400">*</span>
            </span>
          </label>

          <label className="flex items-start gap-2 text-sm theme-text">
            <input
              type="checkbox"
              checked={termsAccepted}
              onChange={(e) => {
                setTermsAccepted(e.target.checked);
                setErrors((prev) => ({ ...prev, termsAccepted: "" }));
              }}
              className="mt-1 rounded border-gray-400 accent-[var(--color-accent)]"
            />
            <span>
              I have read and agree to the{" "}
              <Link
                to="/terms"
                className="underline text-[var(--color-accent)]"
              >
                Terms of Service
              </Link>
              ,{" "}
              <Link
                to="/privacy"
                className="underline text-[var(--color-accent)]"
              >
                Privacy Policy
              </Link>
              , and{" "}
              <Link
                to="/responsible-use"
                className="underline text-[var(--color-accent)]"
              >
                Responsible Use Guide
              </Link>
              . <span className="text-red-400">*</span>
            </span>
          </label>

          <label className="flex items-start gap-2 text-sm theme-text">
            <input
              type="checkbox"
              checked={privacyAccepted}
              onChange={(e) => {
                setPrivacyAccepted(e.target.checked);
                setErrors((prev) => ({ ...prev, privacyAccepted: "" }));
              }}
              className="mt-1 rounded border-gray-400 accent-[var(--color-accent)]"
            />
            <span>
              I consent to the collection and processing of my data as described
              in the{" "}
              <Link
                to="/privacy"
                className="underline text-[var(--color-accent)]"
              >
                Privacy Policy
              </Link>
              . <span className="text-red-400">*</span>
            </span>
          </label>

          {errors.ageConfirmed && (
            <p className="text-[11px] font-medium text-red-500 ml-1">
              {errors.ageConfirmed}
            </p>
          )}
          {errors.termsAccepted && (
            <p className="text-[11px] font-medium text-red-500 ml-1">
              {errors.termsAccepted}
            </p>
          )}
          {errors.privacyAccepted && (
            <p className="text-[11px] font-medium text-red-500 ml-1">
              {errors.privacyAccepted}
            </p>
          )}
        </div>

        {/* reCAPTCHA */}
        <div className="flex flex-col items-center space-y-2 w-full max-w-full overflow-x-auto">
          {recaptchaSiteKey ? (
            <ReCAPTCHA
              key={`recaptcha-${isDark}`}
              ref={recaptchaRef}
              sitekey={recaptchaSiteKey}
              onChange={handleCaptchaChange}
              onExpired={handleCaptchaExpired}
              onErrored={handleCaptchaErrored}
              theme={isDark ? "dark" : "light"}
            />
          ) : (
            <p className="text-sm text-red-500">
              reCAPTCHA is not configured. Add VITE_RECAPTCHA_SITE_KEY to your
              frontend environment.
            </p>
          )}
          {errors.recaptcha && (
            <p className="text-[11px] font-medium text-red-500">
              {errors.recaptcha}
            </p>
          )}
        </div>

        <div className="pt-2">
          <PrimaryButton fullWidth disabled={loading}>
            {loading ? "Creating account..." : signupLabel}
          </PrimaryButton>
        </div>

        <Link to="/" className="block">
          <PrimaryButton variant="secondary" fullWidth disabled={loading}>
            {guestLabel}
          </PrimaryButton>
        </Link>
      </form>

      <p className="mt-4 text-center text-xs theme-text-muted">
        Need help? Contact us at{" "}
        <a
          href="mailto:info@uwenzai.com"
          className="underline text-[var(--color-accent)]"
        >
          info@uwenzai.com
        </a>
        . UwenzAi Limited – Nairobi, Kenya.
      </p>

      <p className="mt-6 text-center text-sm theme-text-muted">
        {alreadyHaveAccountLabel}{" "}
        <Link
          to="/login"
          className="font-medium underline underline-offset-4 transition hover:opacity-80"
          style={{ color: "var(--color-accent)" }}
        >
          {loginLabel}
        </Link>
      </p>
    </div>
  );
}
