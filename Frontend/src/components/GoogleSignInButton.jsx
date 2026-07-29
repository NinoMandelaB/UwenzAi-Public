// src/components/GoogleSignInButton.jsx

import { useEffect, useRef, useState } from "react";
import { ENV } from "../config/constants";
import { googleAuth } from "../services/authService";

export default function GoogleSignInButton({
  onStart,
  onSuccess,
  onError,
  disabled = false,
}) {
  const buttonRef = useRef(null);
  const [internalError, setInternalError] = useState("");
  const initialized = useRef(false);

  // Store callbacks in refs to avoid re‑running the effect
  const callbacksRef = useRef({ onStart, onSuccess, onError });
  callbacksRef.current = { onStart, onSuccess, onError };

  useEffect(() => {
    if (!ENV.GOOGLE_CLIENT_ID) {
      setInternalError("Missing Google client ID.");
      return;
    }

    // Prevent duplicate initialization
    if (initialized.current) return;

    const handleGoogleResponse = async (response) => {
      const { onStart, onSuccess, onError } = callbacksRef.current;
      try {
        onStart?.();
        setInternalError("");
        const result = await googleAuth({
          credential: response.credential,
          preferred_language: "en",
          compliance_version: "v1",
        });
        onSuccess?.(result);
      } catch (err) {
        const message = err.message || "Google sign-in failed.";
        setInternalError(message);
        onError?.(message);
      }
    };

    const initializeGoogle = () => {
      if (!window.google || !buttonRef.current) return;
      try {
        window.google.accounts.id.initialize({
          client_id: ENV.GOOGLE_CLIENT_ID,
          callback: handleGoogleResponse,
          auto_select: false,
          context: "signin",
        });

        // Only clear once, then render
        buttonRef.current.innerHTML = "";
        window.google.accounts.id.renderButton(buttonRef.current, {
          theme: "outline",
          size: "large",
          shape: "pill",
          text: "continue_with",
          width: 260,
        });

        initialized.current = true;
      } catch (err) {
        console.warn("GSI Initialization failed:", err);
      }
    };

    // Attach the script or use existing one
    const existingScript = document.getElementById("google-identity-script");

    if (window.google) {
      initializeGoogle();
    } else if (existingScript) {
      existingScript.addEventListener("load", initializeGoogle);
    } else {
      const script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.id = "google-identity-script";
      script.async = true;
      script.defer = true;
      script.onload = initializeGoogle;
      script.onerror = () => {
        const message = "Failed to load Google script.";
        setInternalError(message);
        onError?.(message);
      };
      document.body.appendChild(script);
    }

    // Cleanup: remove the Google button on unmount
    return () => {
      if (buttonRef.current) {
        buttonRef.current.innerHTML = "";
      }
      const script = document.getElementById("google-identity-script");
      if (script) {
        script.removeEventListener("load", initializeGoogle);
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
