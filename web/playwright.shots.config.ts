import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Screenshot run (shots/capture.spec.ts), separate from the e2e suite: one e2e server with the fake mailer so every
// sign-in screen shows. `SHOTS_DIR=~/tmp/ui-audit/after npm run shots`
const PORT = Number(process.env.SHOTS_PORT ?? 8796);
const REPO_PYTHON = fileURLToPath(new URL("../.venv/bin/python", import.meta.url));
const PYTHON = process.env.PYTHON ?? (existsSync(REPO_PYTHON) ? REPO_PYTHON : "python3");

export default defineConfig({
  testDir: "./shots",
  workers: 1,
  reporter: "list",
  use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${PORT}`,
         launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {} },
  webServer: {
    command: `${PYTHON} ../scripts/e2e_server.py --port ${PORT} --host 127.0.0.1 --fake-mailer --info shots/.server.json`,
    url: `http://127.0.0.1:${PORT}/api/auth/state`,
    env: { GOLDBOT_E2E_FAKE_MAILER: "1" },
    reuseExistingServer: false,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    timeout: 60_000,
  },
});
