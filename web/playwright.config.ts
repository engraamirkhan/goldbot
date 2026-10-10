import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 8790);   // E2E_PORT: run beside another e2e server

// The backend is the real FastAPI app (scripts/e2e_server.py) serving the production build from web/dist,
// so these tests cover the browser, the API contract, auth/TOTP and the approval centre together.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `${process.env.PYTHON ?? "python"} ../scripts/e2e_server.py --port ${PORT} --info e2e/.server.json`,
    url: `http://127.0.0.1:${PORT}/api/auth/state`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
