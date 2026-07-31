const AUTH_MESSAGE_MAP = {
  username_required: "Please enter your username.",
  full_name_required: "Please enter your full name.",
  email_required: "Please enter your email address.",
  password_required: "Please enter your password.",
  confirm_password_required: "Please confirm your password.",
  password_mismatch: "Your passwords do not match.",
  password_too_short: "Your password must be at least 6 characters long.",
  password_too_long: "Your password must not exceed 20 characters.",
  age_confirmation_required:
    "You must confirm that you meet the age requirement.",
  terms_acceptance_required:
    "You must accept the terms of service to continue.",
  privacy_acceptance_required:
    "You must accept the privacy policy to continue.",
  invalid_password: "Your password does not meet the required rules.",
  invalid_email: "Please enter a valid email address.",
  email_already_exists: "An account with that email already exists.",
  username_already_exists: "That username is already taken.",
  invalid_credentials: "The username or password you entered is incorrect.",
  account_locked:
    "Too many failed sign-in attempts. Please wait a while before trying again.",
  account_deleted: "This account is no longer available.",
  account_banned: "This account has been banned. Please contact support.",
  account_suspended: "This account is suspended. Please contact support.",
  account_inactive: "This account is inactive.",
  integrity_error: "We could not complete your request due to a data conflict.",
  server_error: "Something went wrong on our side. Please try again.",

  google_credential_required:
    "Google sign-in could not be completed. Please try again.",
  google_auth_not_configured: "Google sign-in is not available right now.",
  invalid_google_credential:
    "Your Google sign-in session is invalid or expired. Please try again.",
  invalid_google_sub: "We could not verify your Google account.",
  google_email_missing: "Your Google account did not provide an email address.",
  google_email_not_verified:
    "Please verify your Google email address before signing in.",
  invalid_google_email: "The email returned by Google is invalid.",
  google_account_not_linked:
    "This account is not linked to Google. Please sign in using your password.",
  google_signup_not_allowed:
    "You cannot create an account using Google. Please sign up manually first.",
  google_email_mismatch:
    "Your Google account does not match our records. Please contact support.",

  refresh_token_required:
    "Your session could not be refreshed because the refresh token is missing.",
  invalid_refresh_token: "Your session has expired. Please sign in again.",
  user_not_found: "We could not find your account. Please sign in again.",
  user_id_required: "Missing verification reference. Please try again.",

  verification_code_required: "Please enter the verification code.",
  invalid_verification_code: "The verification code is incorrect.",
  verification_not_found: "We could not find a pending verification request.",
  verification_expired:
    "This verification code has expired. Please request a new one.",
  verification_attempts_exceeded:
    "Too many incorrect attempts. Please request a new verification code.",
  verification_email_sent:
    "Your account was created. Please check your email for the verification code.",
  verification_email_resent:
    "A new verification code has been sent to your email.",
  email_verified: "Your email has been verified successfully.",
  email_already_verified: "Your email is already verified.",
  email_send_failed:
    "We could not send the verification email right now. Please try again.",
  email_verification_required:
    "Your email address is not verified yet. Please verify your email before signing in.",

  recovery_code_required: "Please enter the recovery code.",
  invalid_recovery_code: "The recovery code is incorrect.",
  recovery_code_expired:
    "This recovery code has expired. Please request a new one.",
  password_reset_successful: "Your password has been reset successfully.",
  if_account_exists_recovery_email_sent:
    "If an account exists for that email, a recovery code has been sent.",
  password_reset_email_sent: "We’ve sent a password reset code to your email.",

  signup_successful: "Your account has been created successfully.",
  signin_successful: "You have signed in successfully.",
  google_auth_successful: "Google sign-in was successful.",
  signout_successful: "You have signed out successfully.",
  token_refreshed: "Your session has been refreshed.",
  recovery_attempts_exceeded:
    "Too many incorrect recovery code attempts. Please request a new code.",
  current_password_required: "Please enter your current password.",
  new_password_required: "Please enter your new password.",
  current_password_incorrect: "Your current password is incorrect.",
  new_password_must_differ:
    "Your new password must be different from your current password.",
  password_changed_successfully: "Your password has been changed successfully.",
  not_authenticated: "Please sign in again to continue.",
  user_permanently_deleted:
    "Your account has been permanently deleted. This action cannot be undone.",

  // DRF raw message fallback
  "Authentication credentials were not provided.":
    "Please sign in again to continue.",
};

export function getAuthMessage(
  errorOrCode,
  fallback = "Something went wrong.",
) {
  if (!errorOrCode) return fallback;

  if (typeof errorOrCode === "string") {
    return (
      AUTH_MESSAGE_MAP[errorOrCode] || prettifyCode(errorOrCode) || fallback
    );
  }

  const directPayload =
    errorOrCode?.response?.data?.data ??
    errorOrCode?.response?.data?.message ??
    errorOrCode?.data?.data ??
    errorOrCode?.data?.message ??
    errorOrCode?.message;

  if (typeof directPayload === "string") {
    return (
      AUTH_MESSAGE_MAP[directPayload] || prettifyCode(directPayload) || fallback
    );
  }

  if (typeof directPayload === "object" && directPayload) {
    if (
      directPayload.code === "account_locked" &&
      typeof directPayload.retry_after_seconds === "number"
    ) {
      const minutes = Math.ceil(directPayload.retry_after_seconds / 60);
      return `Too many failed sign-in attempts. Try again in about ${minutes} minute${minutes === 1 ? "" : "s"}.`;
    }

    if (
      directPayload.code === "invalid_password" &&
      Array.isArray(directPayload.errors)
    ) {
      return directPayload.errors.join(" ");
    }

    if (typeof directPayload.code === "string") {
      return (
        AUTH_MESSAGE_MAP[directPayload.code] ||
        prettifyCode(directPayload.code) ||
        fallback
      );
    }
  }

  return fallback;
}

export function getAuthTitle(errorOrCode, fallback = "Request failed") {
  if (!errorOrCode) return fallback;

  if (typeof errorOrCode === "string") {
    if (errorOrCode === "account_locked") return "Too many attempts";
    if (errorOrCode === "invalid_credentials") return "Login failed";
    if (errorOrCode === "email_verification_required")
      return "Email verification required";
    if (errorOrCode === "recovery_attempts_exceeded") {
      return "Too many attempts";
    }
    if (errorOrCode === "current_password_incorrect")
      return "Password change failed";
    if (errorOrCode === "new_password_must_differ")
      return "Password change failed";
    if (errorOrCode === "password_changed_successfully")
      return "Password changed";
    if (
      errorOrCode === "invalid_verification_code" ||
      errorOrCode === "verification_expired" ||
      errorOrCode === "verification_attempts_exceeded" ||
      errorOrCode === "verification_not_found"
    ) {
      return "Verification failed";
    }
    if (
      errorOrCode === "invalid_recovery_code" ||
      errorOrCode === "recovery_code_expired" ||
      errorOrCode === "recovery_code_required"
    ) {
      return "Password reset failed";
    }
    if (
      errorOrCode === "if_account_exists_recovery_email_sent" ||
      errorOrCode === "password_reset_email_sent"
    ) {
      return "Recovery email sent";
    }
    if (errorOrCode === "password_reset_successful") {
      return "Password reset successful";
    }
    if (errorOrCode === "verification_email_resent") return "Code sent";
    if (errorOrCode === "email_verified") return "Email verified";
    return fallback;
  }

  const directPayload =
    errorOrCode?.response?.data?.data ??
    errorOrCode?.response?.data?.message ??
    errorOrCode?.data?.data ??
    errorOrCode?.data?.message ??
    errorOrCode?.message;

  if (typeof directPayload === "string") {
    if (directPayload === "account_locked") return "Too many attempts";
    if (directPayload === "invalid_credentials") return "Login failed";
    if (directPayload === "email_verification_required")
      return "Email verification required";
    if (
      directPayload === "invalid_verification_code" ||
      directPayload === "verification_expired" ||
      directPayload === "verification_attempts_exceeded" ||
      directPayload === "verification_not_found"
    ) {
      return "Verification failed";
    }
    if (
      directPayload === "invalid_recovery_code" ||
      directPayload === "recovery_code_expired" ||
      directPayload === "recovery_code_required"
    ) {
      return "Password reset failed";
    }
    if (
      directPayload === "if_account_exists_recovery_email_sent" ||
      directPayload === "password_reset_email_sent"
    ) {
      return "Recovery email sent";
    }
    if (directPayload === "password_reset_successful") {
      return "Password reset successful";
    }
    if (directPayload === "verification_email_resent") return "Code sent";
    if (directPayload === "email_verified") return "Email verified";
    return fallback;
  }

  if (typeof directPayload === "object" && directPayload) {
    if (directPayload.code === "account_locked") {
      return "Too many attempts";
    }

    if (directPayload.code === "invalid_credentials") {
      return "Login failed";
    }

    if (directPayload.code === "email_verification_required") {
      return "Email verification required";
    }

    if (
      directPayload.code === "invalid_verification_code" ||
      directPayload.code === "verification_expired" ||
      directPayload.code === "verification_attempts_exceeded" ||
      directPayload.code === "verification_not_found"
    ) {
      return "Verification failed";
    }

    if (
      directPayload.code === "invalid_recovery_code" ||
      directPayload.code === "recovery_code_expired" ||
      directPayload.code === "recovery_code_required"
    ) {
      return "Password reset failed";
    }

    if (
      directPayload.code === "if_account_exists_recovery_email_sent" ||
      directPayload.code === "password_reset_email_sent"
    ) {
      return "Recovery email sent";
    }

    if (directPayload.code === "password_reset_successful") {
      return "Password reset successful";
    }

    if (directPayload.code === "verification_email_resent") {
      return "Code sent";
    }

    if (directPayload.code === "email_verified") {
      return "Email verified";
    }
  }

  return fallback;
}

function prettifyCode(value) {
  if (typeof value !== "string") return "";
  if (!value.includes("_")) return value;
  const formatted = value.replace(/_/g, " ").trim();
  return formatted.charAt(0).toUpperCase() + formatted.slice(1) + ".";
}
