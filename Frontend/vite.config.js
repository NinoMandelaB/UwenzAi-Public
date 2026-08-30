import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      workbox: {
        // SPA: serve the cached shell for client-side routes on cold starts.
        navigateFallback: "/index.html",
        cleanupOutdatedCaches: true,
        // Push handlers live in public/push-sw.js and are merged into the
        // generated service worker. Kept as importScripts (not injectManifest)
        // so the precache setup above stays generated — the push code carries
        // no build-time dependencies of its own.
        importScripts: ["push-sw.js"],
        // Precache of build assets only — deliberately no runtimeCaching.
        // Every API and media response can carry private chat data, and a
        // service-worker cache on a shared device would outlive logout.
      },
      manifest: {
        name: "UwenzAi — AI Companions Built for East Africa",
        short_name: "UwenzAi",
        description:
          "Intelligent, multilingual (English & Kiswahili) AI companions with memory, relationships, and culturally aware conversations.",
        start_url: "/",
        scope: "/",
        display: "standalone",
        orientation: "portrait",
        // Matches the default dark theme (see index.css :root / data-theme).
        theme_color: "#020617",
        background_color: "#020617",
        icons: [
          { src: "/pwa-192.png", sizes: "192x192", type: "image/png" },
          { src: "/pwa-512.png", sizes: "512x512", type: "image/png" },
          {
            src: "/pwa-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
    }),
  ],
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{js,jsx}"],
    // config/constants.js refuses to load without an API base URL, so any test
    // that reaches something importing it needs one.
    env: { VITE_API_BASE_URL: "http://localhost:8000" },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        secure: false,
      },
      "/ws": {
        // WebSocket connections
        target: "ws://127.0.0.1:8000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
