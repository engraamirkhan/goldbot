import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.E2E_PORT ?? 8790);   // E2E_PORT: run beside another e2e server
const AUTH_PORT = PORT + 1;
// The interpreter for the backend: PYTHON if set (CI), else this checkout's own venv, else python3. A bare `python`
// can be a pyenv shim or another checkout's venv whose editable install imports goldbot from the wrong tree (TD-3);
// scripts/e2e_server.py also refuses to start on a goldbot outside this tree.
const REPO_PYTHON = fileURLToPath(new URL("../.venv/bin/python", import.meta.url));
const PYTHON = process.env.PYTHON ?? (existsSync(REPO_PYTHON) ? REPO_PYTHON : "python3");

// The backend is the real FastAPI app (scripts/e2e_server.py) serving the production build from web/dist,
// so these tests cover the browser, the API contract, auth/TOTP and the approval centre together.
// Two servers, each with its own throwaway state: the dashboard story, and the sign-in story (auth.spec.ts), whose
// server captures the owner's sign-in emails with the e2e-only fake mailer (goldbot/api/e2e_mailer.py: refused unless
// GOLDBOT_E2E_FAKE_MAILER=1 and the server is bound to 127.0.0.1). Each story is serial inside its file (shared state), and the
// two files use different servers and different TOTP clocks (e2e/totp.ts), so two workers run them side by side.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // Safe only while each spec file has its own server and TOTP clock (dashboard.spec.ts: port PORT, auth.spec.ts:
  // AUTH_PORT) and is serial inside. A second spec in the "chromium" project must get its own server, or that
  // project must be made serial (workers: 1), or two files would share one owner, one clock and one set of spent codes.
  workers: 2,
  // 15 s, not the 5 s default: an assertion waits for a poll or a render, and on a shared runner or a laptop running
  // other lanes (load average above 100 measured on 2026-10-30) one API round-trip alone can exceed 5 s. A wait only
  // costs time when the thing it waits for is late; it never makes a wrong page pass.
  expect: { timeout: 15_000 },
  // Retries only label a test "flaky" and CI fails on flaky tests anyway. A serial story (dashboard.spec.ts, auth.spec.ts)
  // sets retries: 0 itself: a retry would replay it from its first test against a server that already has its owner.
  retries: process.env.CI ? 1 : 0,
  // The retry only labels a test "flaky"; in CI a flaky test still fails the build (no retry masking). CI runs one
  // job per project (.github/workflows/ci.yml e2e matrix, tests/test_ci_workflow.py).
  failOnFlakyTests: !!process.env.CI,
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
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },   // lets e2e_server.py remove its temp state dir
      timeout: 60_000,
    },
    {
      command: `${PYTHON} ../scripts/e2e_server.py --port ${AUTH_PORT} --host 127.0.0.1 --fake-mailer --info e2e/.server-auth.json`,
      url: `http://127.0.0.1:${AUTH_PORT}/api/auth/state`,
      env: { GOLDBOT_E2E_FAKE_MAILER: "1" },
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },   // lets e2e_server.py remove its temp state dir
      timeout: 60_000,
    },
  ],
});
