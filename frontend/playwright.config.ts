import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  webServer: {
    command:
      "npm run build && npm run preview -- --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    // E2E runs in no-auth mode: the suite mocks API routes and has no real
    // auth session. Empty values win over frontend/.env in the Vite build.
    env: { VITE_SUPABASE_AUTH_URL: "", VITE_SUPABASE_AUTH_ANON_KEY: "" },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
