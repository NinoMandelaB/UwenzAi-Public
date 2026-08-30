// src/hooks/usePWAInstall.js

import { useCallback, useEffect, useState } from "react";

function detectInstalled() {
  return (
    window.matchMedia?.("(display-mode: standalone)").matches === true ||
    window.navigator.standalone === true
  );
}

/**
 * Install state for the PWA.
 *
 * Chromium fires `beforeinstallprompt` once the app meets the install
 * criteria (HTTPS, manifest, service worker). The event is stashed and
 * replayed from Settings because `prompt()` consumes it — it can only be
 * shown once per event. Browsers that never fire the event (iOS Safari,
 * Firefox) leave `canInstall` false and the install UI stays hidden.
 */
export function usePWAInstall() {
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [isInstalled, setIsInstalled] = useState(detectInstalled);

  useEffect(() => {
    const onBeforeInstallPrompt = (event) => {
      // Suppress the browser's default mini-infobar: the Settings entry point
      // is the single place that triggers the native prompt.
      event.preventDefault();
      setDeferredPrompt(event);
    };
    const onAppInstalled = () => {
      setIsInstalled(true);
      setDeferredPrompt(null);
    };

    window.addEventListener("beforeinstallprompt", onBeforeInstallPrompt);
    window.addEventListener("appinstalled", onAppInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstallPrompt);
      window.removeEventListener("appinstalled", onAppInstalled);
    };
  }, []);

  const promptInstall = useCallback(async () => {
    if (!deferredPrompt) return "unavailable";
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    // Accepted or dismissed, the event is single-use either way.
    setDeferredPrompt(null);
    return outcome;
  }, [deferredPrompt]);

  return {
    canInstall: Boolean(deferredPrompt) && !isInstalled,
    isInstalled,
    promptInstall,
  };
}
