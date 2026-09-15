import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests against the real FeedbackIQ API.
 *
 * Nothing about authentication is faked here: a real Chromium, the built app, the real FastAPI
 * backend and a real PostgreSQL, with real HttpOnly cookies. The API must already be running (see
 * web/README.md for the local commands; CI starts it in the `e2e` job).
 *
 * By default Playwright builds the app and serves it with `vite preview`, whose /api proxy points at
 * FEEDBACKIQ_API_URL (default http://localhost:8000). Set E2E_BASE_URL to test an app that is
 * already running instead, e.g. the nginx container on http://localhost:8080.
 */
const externalApp = process.env.E2E_BASE_URL;
const baseURL = externalApp ?? "http://localhost:4173";

export default defineConfig({
  testDir: "./e2e",
  // One worker: the tests share one backend and database, and clarity beats speed here.
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: externalApp
    ? undefined
    : {
        command: "npm run build && npm run preview",
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
