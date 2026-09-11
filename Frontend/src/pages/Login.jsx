// src/pages/Login.jsx

import { Link, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import ReCAPTCHA from "react-google-recaptcha";
import { Lock, ShieldCheck, User, Eye, EyeOff } from "lucide-react";
import PrimaryButton from "../components/PrimaryButton";
import GoogleSignInButton from "../components/GoogleSignInButton";
import MessagePopup from "../components/MessagePopup";
import { useLanguage } from "../context/LanguageContext";
import { signinUser } from "../services/authService";
import { getAuthSessionGeneration } from "../services/apiClient";
import { useAuth } from "../context/AuthContext";
import { useTheme } from "../context/ThemeContext";
import { getAuthMessage, getAuthTitle } from "../utils/authMessageMapper";

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
          aria-label={label}
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

function safeT(t, key, fallback) {
  const value = t(key);
  return value && value !== key ? value : fallback;
}

export default function Login() {
  const { t, language } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const { isDark } = useTheme(); // only need isDark for reCAPTCHA theme

  const recaptchaRef = useRef(null);
  const requestController = useRef(null);
  useEffect(() => {
    requestController.current = new AbortController();
    return () => requestController.current.abort();
  }, []);
  const recaptchaSiteKey = import.meta.env.VITE_RECAPTCHA_SITE_KEY || "";

  const [formData, setFormData] = useState({
    username: "",
    password: "",
  });

  const [captchaToken, setCaptchaToken] = useState("");
  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  // Navigation intent only. Google credentials are never carried across login.
  const connectGoogleAfterLogin = location.state?.connectGoogle === true;
  const setConnectGoogleAfterLogin = (enabled) => {
    navigate("/login", { replace: true, state: { connectGoogle: enabled } });
  };

  const [popup, setPopup] = useState({
    open: false,
    type: "info",
    title: "",
    message: "",
  });

  const usernameLabel = safeT(t, "auth.username", "Username");
  const passwordLabel = safeT(t, "auth.password", "Password");
  const welcomeBackLabel = safeT(t, "auth.welcomeBack", "Welcome back");
  const welcomeBackSubtitle = safeT(
    t,
    "auth.welcomeBackSubtitle",
    "Sign in to continue your conversations.",
  );
  const continueWithLabel = safeT(t, "common.continueWith", "Continue with");
  const noAccountLabel = safeT(t, "auth.noAccount", "Don’t have an account?");
  const signupLabel = safeT(t, "common.signup", "Sign up");
  const loginLabel = safeT(t, "common.login", "Login");
  const guestLabel = safeT(t, "common.continueAsGuest", "Continue as Guest");

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

  const resetRecaptcha = () => {
    recaptchaRef.current?.reset();
    setCaptchaToken("");
  };

  const validate = () => {
    const nextErrors = {};
    const username = formData.username.trim().toLowerCase();
    const password = formData.password;

    if (!username) {
      nextErrors.username = "Username is required.";
    }

    if (!password) {
      nextErrors.password = "Password is required.";
    }

    if (!recaptchaSiteKey) {
      nextErrors.recaptcha =
        "reCAPTCHA is not configured. Please try again later.";
    } else if (!captchaToken) {
      nextErrors.recaptcha = "Please confirm that you are not a robot.";
    }

    return nextErrors;
  };

  const handleChange = (event) => {
    const { name, value } = event.target;

    setFormData((prev) => ({
      ...prev,
      [name]: value,
    }));

    setErrors((prev) => ({
      ...prev,
      [name]: "",
    }));

    closePopup();
  };

  const handleCaptchaChange = (token) => {
    setCaptchaToken(token || "");

    setErrors((prev) => ({
      ...prev,
      recaptcha: "",
    }));

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
    if (loading || googleLoading) return;

    const nextErrors = validate();
    setErrors(nextErrors);
    closePopup();

    if (Object.keys(nextErrors).length > 0) {
      return;
    }

    setLoading(true);
    const controller = requestController.current;
    const generation = getAuthSessionGeneration();
    const current = () =>
      !controller.signal.aborted && generation === getAuthSessionGeneration();

    try {
      const result = await signinUser(
        {
          username: formData.username.trim().toLowerCase(),
          password: formData.password,
          recaptcha_token: captchaToken,
        },
        { signal: controller.signal },
      );
      if (!current()) return;

      login({
        user: result?.data?.user || null,
        accessToken: result?.data?.access_token || null,
      });

      navigate(
        connectGoogleAfterLogin ? "/settings#google-connection" : "/characters",
      );
    } catch (err) {
      if (!current() || err.name === "AbortError") return;
      resetRecaptcha();

      const verificationPayload = err?.payload?.data;

      if (verificationPayload?.code === "email_verification_required") {
        navigate("/verify-email", {
          state: {
            user_id: verificationPayload.user_id || "",
            email: verificationPayload.email || "",
          },
        });
        return;
      }

      showPopup({
        type: "error",
        title: getAuthTitle(err, "Login failed"),
        message: getAuthMessage(
          err,
          "Unable to sign in right now. Please try again.",
        ),
      });
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  };

  const handleGoogleSuccess = (result) => {
    setGoogleLoading(false);
    closePopup();

    login({
      user: result?.data?.user || null,
      accessToken: result?.data?.access_token || null,
    });

    navigate("/characters");
  };

  const handleGoogleStart = () => {
    closePopup();
    setGoogleLoading(true);
  };

  const handleGoogleError = (message) => {
    setGoogleLoading(false);
    if (message?.code === "google_link_confirmation_required") {
      setConnectGoogleAfterLogin(true);
      closePopup();
      return;
    }

    showPopup({
      type: "error",
      title: "Google sign-in failed",
      message: getAuthMessage(
        message,
        "Unable to continue with Google right now.",
        language,
      ),
    });
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
          {welcomeBackLabel}
        </h2>
        <p className="mt-2 text-sm leading-6 theme-text-muted">
          {welcomeBackSubtitle}
        </p>
      </div>

      {connectGoogleAfterLogin && (
        <div role="status" className="mb-5 rounded-2xl border p-4 theme-card">
          <p className="text-sm leading-6 theme-text-muted">
            {getAuthMessage("google_link_confirmation_required", "", language)}
          </p>
          <button
            type="button"
            onClick={() => setConnectGoogleAfterLogin(false)}
            className="mt-2 text-sm underline theme-text"
          >
            {t("common.cancel")}
          </button>
        </div>
      )}

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
          name="password"
          label={passwordLabel}
          type="password"
          value={formData.password}
          onChange={handleChange}
          autoComplete="current-password"
          icon={Lock}
          error={errors.password}
        />

        {/* Forgot password link */}
        <div className="flex justify-end">
          <Link
            to="/password-recovery"
            state={{ email: formData.username }}
            className="text-xs font-medium underline underline-offset-4 transition hover:opacity-80"
            style={{ color: "var(--color-accent)" }}
          >
            Forgot password?
          </Link>
        </div>

        {/* reCAPTCHA – theme‑aware, responsive */}
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
          <PrimaryButton fullWidth disabled={loading || googleLoading}>
            {loading ? "Signing in..." : loginLabel}
          </PrimaryButton>
        </div>

        <div className="relative py-2">
          <div className="absolute inset-0 flex items-center">
            <div
              className="w-full border-t"
              style={{ borderColor: "var(--color-border)" }}
            />
          </div>

          <div className="relative flex justify-center">
            <span
              className="px-3 text-xs font-bold uppercase tracking-widest theme-text-muted"
              style={{ backgroundColor: "var(--color-shell)" }}
            >
              {continueWithLabel}
            </span>
          </div>
        </div>

        <div className="flex justify-center">
          <GoogleSignInButton
            onStart={handleGoogleStart}
            onSuccess={handleGoogleSuccess}
            onError={handleGoogleError}
            disabled={loading || googleLoading}
          />
        </div>

        <Link to="/" className="block">
          <PrimaryButton
            variant="secondary"
            fullWidth
            disabled={loading || googleLoading}
          >
            {guestLabel}
          </PrimaryButton>
        </Link>
      </form>

      <p className="mt-8 text-center text-sm theme-text-muted">
        {noAccountLabel}{" "}
        <Link
          to="/register"
          className="font-medium underline underline-offset-4 transition hover:opacity-80"
          style={{ color: "var(--color-accent)" }}
        >
          {signupLabel}
        </Link>
      </p>
    </div>
  );
}
