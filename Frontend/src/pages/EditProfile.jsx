import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  User,
  Phone,
  Globe,
  Check,
  Mail,
  Lock,
  X,
} from "lucide-react";
import PrimaryButton from "../components/PrimaryButton";
import { useAuth } from "../context/AuthContext";
import { useLanguage } from "../context/LanguageContext";
import {
  updateProfile,
  requestEmailChange,
  confirmEmailChange,
} from "../services/authService";

export default function EditProfile() {
  const navigate = useNavigate();
  const { user, setUser } = useAuth();
  const { t } = useLanguage();

  // Profile fields
  const [username, setUsername] = useState(user?.username || "");
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [phoneNumber, setPhoneNumber] = useState(user?.phone_number || "");
  const [preferredLang, setPreferredLang] = useState(
    user?.preferred_language || "en",
  );

  // UI state
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  // Email change modal state
  const [showEmailModal, setShowEmailModal] = useState(false);
  const [emailStep, setEmailStep] = useState("request"); // "request" or "verify"
  const [newEmail, setNewEmail] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [emailError, setEmailError] = useState("");
  const [emailSubmitting, setEmailSubmitting] = useState(false);

  const langOptions = [
    { value: "en", label: "English" },
    { value: "sw", label: "Swahili" },
    { value: "sh", label: "Sheng" },
  ];

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError("");

    try {
      const payload = {
        username: username.trim(),
        display_name: displayName.trim(),
        phone_number: phoneNumber.trim() || undefined,
        preferred_language: preferredLang,
      };

      const result = await updateProfile(payload);
      const updatedUser = result?.data?.user;
      if (updatedUser) {
        setUser(updatedUser);
      }
      setSuccess(true);
      setTimeout(() => navigate(-1), 1500);
    } catch (err) {
      setError(err.message || "Failed to update profile");
    } finally {
      setSubmitting(false);
    }
  };

  // Email change: step 1 – request code
  const handleRequestEmailChange = async () => {
    if (!newEmail) {
      setEmailError("Please enter a new email address");
      return;
    }
    setEmailSubmitting(true);
    setEmailError("");
    try {
      await requestEmailChange({ new_email: newEmail });
      setEmailStep("verify");
    } catch (err) {
      setEmailError(err.message || "Failed to send verification code");
    } finally {
      setEmailSubmitting(false);
    }
  };

  // Email change: step 2 – verify code
  const handleConfirmEmailChange = async () => {
    if (!verificationCode) {
      setEmailError("Please enter the verification code");
      return;
    }
    setEmailSubmitting(true);
    setEmailError("");
    try {
      const result = await confirmEmailChange({ code: verificationCode });
      const updatedUser = result?.data?.user;
      if (updatedUser) {
        setUser(updatedUser);
      }
      // Close modal and show success message on main screen
      setShowEmailModal(false);
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setEmailError(err.message || "Verification failed");
    } finally {
      setEmailSubmitting(false);
    }
  };

  const resetEmailModal = () => {
    setShowEmailModal(false);
    setEmailStep("request");
    setNewEmail("");
    setVerificationCode("");
    setEmailError("");
    setEmailSubmitting(false);
  };

  return (
    <div className="max-w-2xl px-4 py-6 sm:px-6 lg:px-8 space-y-6">
      {/* Back button */}
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-2 text-sm font-medium theme-text-muted hover:underline"
      >
        <ArrowLeft size={16} />
        Back
      </button>

      <div>
        <h2 className="text-2xl font-semibold theme-text">Edit Profile</h2>
        <p className="mt-1 text-sm theme-text-muted">
          Update your personal details.
        </p>
      </div>

      {success && (
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-300">
          Profile updated successfully!
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5">
        {/* Username (editable) */}
        <div>
          <label className="mb-2 block text-sm font-medium theme-text">
            Username
          </label>
          <div className="flex items-center gap-3 rounded-2xl border px-4 py-3 theme-input">
            <User size={18} className="text-[var(--color-text-muted)]" />
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-transparent text-sm outline-none theme-text"
              placeholder="username"
            />
          </div>
        </div>

        {/* Screen Name */}
        <div>
          <label className="mb-2 block text-sm font-medium theme-text">
            Screen Name
          </label>
          <div className="flex items-center gap-3 rounded-2xl border px-4 py-3 theme-input">
            <User size={18} className="text-[var(--color-text-muted)]" />
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full bg-transparent text-sm outline-none theme-text"
            />
          </div>
        </div>

        {/* Email (display + change button) */}
        <div>
          <label className="mb-2 block text-sm font-medium theme-text">
            Email Address
          </label>
          <div className="flex items-center gap-3 rounded-2xl border px-4 py-3 theme-input">
            <Mail size={18} className="text-[var(--color-text-muted)]" />
            <span className="flex-1 text-sm theme-text">{user?.email}</span>
            <button
              type="button"
              onClick={() => setShowEmailModal(true)}
              className="text-sm font-medium text-[var(--color-accent)] hover:underline"
            >
              Change
            </button>
          </div>
        </div>

        {/* Phone Number */}
        <div>
          <label className="mb-2 block text-sm font-medium theme-text">
            Phone Number
          </label>
          <div className="flex items-center gap-3 rounded-2xl border px-4 py-3 theme-input">
            <Phone size={18} className="text-[var(--color-text-muted)]" />
            <input
              type="tel"
              value={phoneNumber}
              onChange={(e) => setPhoneNumber(e.target.value)}
              className="w-full bg-transparent text-sm outline-none theme-text"
            />
          </div>
        </div>

        {/* Preferred Language */}
        <div>
          <label className="mb-2 block text-sm font-medium theme-text">
            Preferred Language
          </label>
          <div className="flex gap-2">
            {langOptions.map((lang) => (
              <button
                key={lang.value}
                type="button"
                onClick={() => setPreferredLang(lang.value)}
                className={`flex-1 rounded-full py-2 text-sm font-medium transition ${
                  preferredLang === lang.value
                    ? "bg-[var(--color-accent)] text-[#082f49]"
                    : "bg-[var(--color-soft)] text-[var(--color-text-muted)] border border-[var(--color-border)]"
                }`}
              >
                {lang.label}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 p-3 text-sm text-rose-300">
            {error}
          </div>
        )}

        <PrimaryButton type="submit" fullWidth disabled={submitting}>
          {submitting ? "Saving…" : "Save Changes"}
        </PrimaryButton>
      </form>

      {/* Email Change Modal */}
      {showEmailModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl bg-[var(--color-bg-primary)] p-6 shadow-xl border border-[var(--color-border)]">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-xl font-semibold theme-text">
                {emailStep === "request"
                  ? "Change Email Address"
                  : "Verify New Email"}
              </h3>
              <button
                onClick={resetEmailModal}
                className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                <X size={20} />
              </button>
            </div>

            {emailStep === "request" ? (
              <>
                <p className="text-sm theme-text-muted mb-4">
                  We'll send a verification code to the new email address.
                </p>
                <input
                  type="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  placeholder="new@example.com"
                  className="w-full rounded-xl border px-4 py-3 theme-input mb-4"
                  autoFocus
                />
                {emailError && (
                  <p className="text-sm text-rose-400 mb-3">{emailError}</p>
                )}
                <div className="flex gap-3">
                  <button
                    onClick={resetEmailModal}
                    className="flex-1 rounded-full border border-[var(--color-border)] py-2 text-sm font-medium theme-text-muted hover:bg-[var(--color-soft)]"
                  >
                    Cancel
                  </button>
                  <PrimaryButton
                    onClick={handleRequestEmailChange}
                    disabled={emailSubmitting}
                    className="flex-1"
                  >
                    {emailSubmitting ? "Sending..." : "Send Code"}
                  </PrimaryButton>
                </div>
              </>
            ) : (
              <>
                <p className="text-sm theme-text-muted mb-2">
                  A verification code was sent to <strong>{newEmail}</strong>.
                </p>
                <p className="text-xs theme-text-muted mb-4">
                  Please enter the 6‑digit code.
                </p>
                <input
                  type="text"
                  value={verificationCode}
                  onChange={(e) => setVerificationCode(e.target.value)}
                  placeholder="000000"
                  className="w-full rounded-xl border px-4 py-3 theme-input mb-4"
                  autoFocus
                  maxLength={6}
                />
                {emailError && (
                  <p className="text-sm text-rose-400 mb-3">{emailError}</p>
                )}
                <div className="flex gap-3">
                  <button
                    onClick={() => setEmailStep("request")}
                    className="flex-1 rounded-full border border-[var(--color-border)] py-2 text-sm font-medium theme-text-muted hover:bg-[var(--color-soft)]"
                  >
                    Back
                  </button>
                  <PrimaryButton
                    onClick={handleConfirmEmailChange}
                    disabled={emailSubmitting}
                    className="flex-1"
                  >
                    {emailSubmitting ? "Verifying..." : "Verify & Update"}
                  </PrimaryButton>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
