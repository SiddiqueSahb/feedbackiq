import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Where the FastAPI backend is, for the dev server and `vite preview` only. The built app never
// knows this: it always calls its own origin at /api, and something in front of it forwards
// those requests - this proxy in development, nginx in the container (web/nginx.conf).
//
// That single-origin shape is what keeps authentication simple. The browser only ever talks to
// one origin, so the backend's HttpOnly session cookie is first-party and no CORS is involved.
// `changeOrigin: false` keeps the browser's Host header, so the backend's own-origin check
// (api/deps.py::check_origin) sees the same origin the browser sends in `Origin`.
const apiTarget = process.env.FEEDBACKIQ_API_URL ?? "http://localhost:8000";

const apiProxy = {
  "/api": { target: apiTarget, changeOrigin: false },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: apiProxy,
  },
  preview: {
    port: 4173,
    strictPort: true,
    proxy: apiProxy,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
