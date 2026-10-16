import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 8790);   // E2E_PORT: run beside another e2e server
const AUTH_PORT = PORT + 1;
const PYTHON = process.env.PYTHON ?? "python";

// The backend is the real FastAPI app (scripts/e2e_server.py) serving the production build from web/dist,
// so these tests cover the browser, the API contract, auth/TOTP and the approval centre together.
// Two servers, each with its own throwaway state: the dashboard story, and the sign-in story (auth.spec.ts), whose
// server captures the owner's sign-in emails with the e2e-only fake mailer (goldbot/api/e2e_mailer.py: refused unless
// GOLDBOT_E2E_FAKE_MAILER=1 and the server is bound to 127.0.0.1). One worker per story, the two side by side.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 2,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    trace: "retain-on-failure",
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  projects: [
    { name: "chromium", testIgnore: /auth\.spec\.ts/, use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${PORT}` } },
    { name: "auth", testMatch: /auth\.spec\.ts/, use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${AUTH_PORT}` } },
  ],
  webServer: [
    {
      command: `${PYTHON} ../scripts/e2e_server.py --port ${PORT} --info e2e/.server.json`,
      url: `http://127.0.0.1:${PORT}/api/auth/state`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `${PYTHON} ../scripts/e2e_server.py --port ${AUTH_PORT} --host 127.0.0.1 --fake-mailer --info e2e/.server-auth.json`,
      url: `http://127.0.0.1:${AUTH_PORT}/api/auth/state`,
      env: { GOLDBOT_E2E_FAKE_MAILER: "1" },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
