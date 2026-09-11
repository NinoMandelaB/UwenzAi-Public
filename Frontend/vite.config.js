import { defineConfig, loadEnv } from "vite";
import { seoAssets } from "./config/seoAssets";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    tailwindcss(),
    seoAssets(loadEnv(mode, ".", "").SEO_PROFILE || "staging"),
    VitePWA({
      registerType: "autoUpdate",
      workbox: {
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [
          /^\/robots\.txt$/,
          /^\/sitemap\.xml$/,
          /^\/llms\.txt$/,
          /^\/ai\.txt$/,
          /^\/manifest\.webmanifest$/,
        ],
        cleanupOutdatedCaches: true,
        importScripts: ["push-sw.js"],
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
}));
