import { useState } from "react";
import { KeyRound, Lock, LockKeyhole } from "lucide-react";
import { useNavigate } from "react-router-dom";
import PrimaryButton from "../components/PrimaryButton";
import MessagePopup from "../components/MessagePopup";
import { useLanguage } from "../context/LanguageContext";
import { useAuth } from "../context/AuthContext";
import { changePassword } from "../services/authService";
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

export default function ChangePassword() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const { accessToken } = useAuth();

  const [formData, setFormData] = useState({
    current_password: "",
    new_password: "",
    confirm_password: "",
  });

  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(false);

  const [popup, setPopup] = useState({
    open: false,
    type: "info",
    title: "",
    message: "",
  });

  const pageTitle = safeT(t, "auth.changePassword", "Change password");
  const pageSubtitle = safeT(
    t,
    "auth.changePasswordSubtitle",
    "Update your password to keep your account secure.",
  );

  const currentPasswordLabel = safeT(
    t,
    "auth.currentPassword",
    "Current Password",
  );
  const newPasswordLabel = safeT(t, "auth.newPassword", "New Password");
  const confirmPasswordLabel = safeT(
    t,
    "auth.confirmNewPassword",
    "Confirm New Password",
  );
  const submitLabel = safeT(t, "auth.changePassword", "Change password");
  const cancelLabel = safeT(t, "common.cancel", "Cancel");

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

  const validate = () => {
    const nextErrors = {};

    if (!formData.current_password) {
      nextErrors.current_password = "Please enter your current password.";
    }

    if (!formData.new_password) {
      nextErrors.new_password = "Please enter your new password.";
    } else if (formData.new_password.length < 6) {
      nextErrors.new_password =
        "Your password must be at least 6 characters long.";
    } else if (formData.new_password.length > 20) {
      nextErrors.new_password = "Your password must not exceed 20 characters.";
    }

    if (!formData.confirm_password) {
      nextErrors.confirm_password = "Please confirm your new password.";
    } else if (formData.new_password !== formData.confirm_password) {
      nextErrors.confirm_password = "Your passwords do not match.";
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

  const handleSubmit = async (event) => {
    event.preventDefault();

    const nextErrors = validate();
    setErrors(nextErrors);
    closePopup();

    if (Object.keys(nextErrors).length > 0) return;

    setLoading(true);

    try {
      const result = await changePassword(
        {
          current_password: formData.current_password,
          new_password: formData.new_password,
          confirm_password: formData.confirm_password,
        },
        accessToken,
      );

      showPopup({
        type: "success",
        title: getAuthTitle(result?.data?.message, "Password changed"),
        message: getAuthMessage(
          result?.data?.message,
          "Your password has been changed successfully.",
        ),
      });

      setFormData({
        current_password: "",
        new_password: "",
        confirm_password: "",
      });

      setTimeout(() => {
        navigate("/settings");
      }, 1200);
    } catch (err) {
      showPopup({
        type: "error",
        title: getAuthTitle(err, "Password change failed"),
        message: getAuthMessage(
          err,
          "We could not change your password right now. Please try again.",
        ),
      });
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = () => {
    navigate("/settings");
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
          {pageSubtitle}
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
          <KeyRound
            size={18}
            style={{
              color: "var(--color-accent)",
              flexShrink: 0,
              marginTop: 2,
            }}
          />
          <div>
            <p className="text-sm font-medium theme-text">Password rules</p>
            <p className="mt-1 text-sm theme-text-muted">
              Use 6 to 20 characters and choose something you will remember.
            </p>
          </div>
        </div>
      </div>

      <form className="space-y-5" onSubmit={handleSubmit}>
        <Input
          name="current_password"
          label={currentPasswordLabel}
          type="password"
          value={formData.current_password}
          onChange={handleChange}
          autoComplete="current-password"
          icon={Lock}
          error={errors.current_password}
        />

        <Input
          name="new_password"
          label={newPasswordLabel}
          type="password"
          value={formData.new_password}
          onChange={handleChange}
          autoComplete="new-password"
          icon={KeyRound}
          error={errors.new_password}
          hint="Use 6 to 20 characters."
          maxLength={20}
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
          maxLength={20}
        />

        <div className="grid grid-cols-1 gap-3 pt-1 sm:grid-cols-2">
          <PrimaryButton fullWidth type="submit" disabled={loading}>
            {loading ? "Updating..." : submitLabel}
          </PrimaryButton>

          <PrimaryButton
            type="button"
            variant="secondary"
            fullWidth
            disabled={loading}
            onClick={handleCancel}
          >
            {cancelLabel}
          </PrimaryButton>
        </div>
      </form>
    </div>
  );
}
