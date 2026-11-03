import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

const engine = process.env.SODAUDIO_ENGINE || "http://127.0.0.1:8770";

export default defineConfig({
  plugins: [
    react(),
    // The service worker pre-caches only the app shell; /api (data, audio, the event socket) is never cached.
    VitePWA({
      registerType: "autoUpdate",
      manifest: false,
      includeAssets: ["icon-192.png", "icon-512.png", "manifest.webmanifest"],
      workbox: {
        globPatterns: ["**/*.{js,css,html,png,svg,webmanifest}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api/, /^\/health/],
        runtimeCaching: [],
      },
    }),
  ],
  server: { port: 5173, proxy: { "/api": { target: engine, ws: true, changeOrigin: false } } },
  build: { outDir: "dist", chunkSizeWarningLimit: 1500 },
});
