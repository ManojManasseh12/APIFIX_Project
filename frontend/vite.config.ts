import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api/": "http://localhost:8000",
    },
  },
  // `vite preview` (used for production checks and the e2e server) needs the
  // same proxy, otherwise /api requests 404 against the static preview server.
  preview: {
    proxy: {
      "/api/": "http://localhost:8000",
    },
  },
});
