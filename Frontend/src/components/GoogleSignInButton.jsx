// src/components/GoogleSignInButton.jsx

import { useEffect, useRef, useState } from "react";
import { ENV } from "../config/constants";
import { googleAuth } from "../services/authService";
import { getAuthSessionGeneration } from "../services/apiClient";
import { getAuthMessage } from "../utils/authMessageMapper";

export default function GoogleSignInButton({
  onStart,
  onSuccess,
  onError,
  onCredential,
  disabled = false,
}) {
  const buttonRef = useRef(null);
  const [internalError, setInternalError] = useState("");

  // Store callbacks in refs to avoid re‑running the effect
  const callbacksRef = useRef({
    onStart,
    onSuccess,
    onError,
    onCredential,
    disabled,
  });
  callbacksRef.current = {
    onStart,
    onSuccess,
    onError,
    onCredential,
    disabled,
  };

  useEffect(() => {
    if (!ENV.GOOGLE_CLIENT_ID) {
      setInternalError("Missing Google client ID.");
      return;
    }

    const controller = new AbortController();
    const generation = getAuthSessionGeneration();
    const current = () =>
      !controller.signal.aborted && generation === getAuthSessionGeneration();
    let busy = false;
    const container = buttonRef.current;

    const handleGoogleResponse = async (response) => {
      if (!current() || busy || callbacksRef.current.disabled) return;
      const { onStart, onSuccess, onError, onCredential } =
        callbacksRef.current;
      busy = true;
      try {
        if (!response?.credential)
          throw new Error("Google did not return a credential.");
        onStart?.();
        setInternalError("");
        if (onCredential) {
          // Settings receives an in-memory selection, not an automatic link.
          onCredential(response.credential);
        } else {
          const result = await googleAuth(
            { credential: response.credential },
            { signal: controller.signal },
          );
          if (current()) onSuccess?.(result);
        }
      } catch (err) {
        if (!current() || err.name === "AbortError") return;
        if (onError) onError(err);
        else setInternalError(getAuthMessage(err, "Google sign-in failed."));
      } finally {
        busy = false;
      }
    };

    const initializeGoogle = () => {
      if (!current() || !window.google || !container) return;
      try {
        window.google.accounts.id.initialize({
          client_id: ENV.GOOGLE_CLIENT_ID,
          callback: handleGoogleResponse,
          auto_select: false,
          context: "signin",
        });

        // Only clear once, then render
        container.innerHTML = "";
        window.google.accounts.id.renderButton(container, {
          theme: "outline",
          size: "large",
          shape: "pill",
          text: "continue_with",
          width: 260,
        });
      } catch (err) {
        setInternalError(
          "Google sign-in could not be loaded. Please try again.",
        );
      }
    };

    // Attach the script or use existing one
    let script = document.getElementById("google-identity-script");
    const handleScriptError = () => {
      if (current())
        setInternalError("Failed to load Google sign-in. Please reload.");
    };

    if (window.google) {
      initializeGoogle();
    } else {
      const isNew = !script;
      if (isNew) {
        script = document.createElement("script");
        script.src = "https://accounts.google.com/gsi/client";
        script.id = "google-identity-script";
        script.async = true;
        script.defer = true;
      }
      script.addEventListener("load", initializeGoogle);
      script.addEventListener("error", handleScriptError);
      if (isNew) document.body.appendChild(script);
    }

    // Cleanup: remove the Google button on unmount
    return () => {
      controller.abort();
      if (container) container.innerHTML = "";
      if (script) {
        script.removeEventListener("load", initializeGoogle);
        script.removeEventListener("error", handleScriptError);
      }
    };
  }, []); // ← now runs only once

  return (
    <div className="flex flex-col items-center gap-2">
      <div
        ref={buttonRef}
        className="min-h-[44px]"
        style={{
          opacity: disabled ? 0.6 : 1,
          pointerEvents: disabled ? "none" : "auto",
        }}
      />
      {internalError && (
        <p className="text-xs font-medium text-red-600 mt-1">{internalError}</p>
      )}
    </div>
  );
}
