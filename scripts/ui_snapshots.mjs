#!/usr/bin/env node
// Screenshots of every dashboard screen and tab, for before/after review of any UI change (docs/RUNBOOK.md,
// "UI snapshots"). Starts its own scripts/e2e_server.py (seeded e2e data, throwaway state, loopback only), signs in
// as a fresh owner, and captures desktop 1440 and phone 390 (UI_SHOTS_VIEWPORTS adds `narrow` 360) in light and dark.
//
//   npm --prefix web run build            # once: the server serves web/dist
//   node scripts/ui_snapshots.mjs before  # ... change the UI, rebuild ...
//   node scripts/ui_snapshots.mjs after   # then compare ~/tmp/ui-shots/before and ~/tmp/ui-shots/after
//
// Output: ~/tmp/ui-shots/<label>/<viewport>-<theme>/NN-<screen>.png (label defaults to a timestamp). Nothing is
// written inside the repo. Full-page shots stretch the page, so a fixed bottom bar lands mid-page and the sticky
// desktop rail ends at the first viewport height; each viewport also gets `fold-*.png`, exactly what the owner sees
// on opening a screen. Env: PYTHON (backend interpreter), UI_SHOTS_PORT, PW_CHROMIUM_PATH, UI_SHOTS_VIEWPORTS.
import { spawn } from "node:child_process";
import { createHmac } from "node:crypto";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(ROOT, "web", "package.json"));
const { chromium } = require("@playwright/test");

const label = process.argv[2] ?? new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
if (!/^[\w.-]+$/.test(label)) throw new Error(`label must be a plain name, got ${label}`);
const OUT = join(homedir(), "tmp", "ui-shots", label);
const PORT = Number(process.env.UI_SHOTS_PORT ?? 8795);
const BASE = `http://127.0.0.1:${PORT}`;
const ALL_VP = { desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 }, narrow: { width: 360, height: 740 } };
const VPS = (process.env.UI_SHOTS_VIEWPORTS ?? "desktop,phone").split(",").map((s) => s.trim()).filter((s) => s in ALL_VP);
const THEMES = ["light", "dark"];
const SECTIONS = ["home", "trades", "chart", "limits", "research", "feeds", "settings"];
const FOLD = ["home", "trades"];      // the screens the owner opens to decide

if (!existsSync(join(ROOT, "web", "dist", "index.html"))) {
  console.error("web/dist is missing: run `npm --prefix web run build` first.");
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

// ------------------------------------------------------------------------------------------------- backend
const venv = join(ROOT, ".venv", "bin", "python");
const [cmd, pre] = process.env.PYTHON ? [process.env.PYTHON, []]
  : existsSync(venv) ? [venv, []] : ["uv", ["run", "--project", ROOT, "python"]];
const info = join(OUT, "server.json");
const server = spawn(cmd, [...pre, join(ROOT, "scripts", "e2e_server.py"), "--port", String(PORT), "--info", info],
  { cwd: ROOT, stdio: ["ignore", "inherit", "inherit"] });
const stop = () => { if (server.exitCode == null) server.kill("SIGTERM"); };
process.on("exit", stop);
process.on("SIGINT", () => { stop(); process.exit(130); });

async function waitUp() {
  for (let i = 0; i < 240; i++) {
    if (server.exitCode != null) throw new Error(`e2e_server exited (${server.exitCode})`);
    try { if ((await fetch(`${BASE}/`)).ok && existsSync(info)) return; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("e2e_server did not start in 120 s");
}

// RFC 6238, as web/e2e/totp.ts; the e2e server's TOTP clock is real time until a test steps it
function totp(secret) {
  const a = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/=+$/, "").toUpperCase()) bits += a.indexOf(c).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const ctr = Buffer.alloc(8);
  ctr.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const m = createHmac("sha1", key).update(ctr).digest();
  const o = m[m.length - 1] & 15;
  return String((m.readUInt32BE(o) & 0x7fffffff) % 1e6).padStart(6, "0");
}

// ------------------------------------------------------------------------------------------------- capture
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
let browser;
const ctx = (vp, theme) => browser.newContext({
  viewport: ALL_VP[vp], colorScheme: theme, deviceScaleFactor: 1, isMobile: vp !== "desktop", hasTouch: vp !== "desktop",
});
async function shot(page, dir, n, name, fullPage = true) {
  mkdirSync(join(OUT, dir), { recursive: true });
  await page.waitForTimeout(700);
  const file = join(OUT, dir, `${String(n).padStart(2, "0")}-${name}.png`);
  await page.screenshot({ path: file, fullPage });
  console.log(file);
}

try {
  await waitUp();
  const { setup_code: setupCode } = JSON.parse(readFileSync(info, "utf8"));
  browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM_PATH || undefined });

  for (const vp of VPS) for (const th of THEMES) {      // first run, before the owner exists
    const c = await ctx(vp, th); const p = await c.newPage(); await p.goto(`${BASE}/`); await p.waitForTimeout(800);
    await shot(p, `${vp}-${th}`, 1, "first-run"); await c.close();
  }
  // create the owner the way a person would, then sign in once
  const c0 = await ctx("desktop", "light"); const p0 = await c0.newPage(); await p0.goto(`${BASE}/`);
  await p0.getByLabel("Setup code").fill(setupCode);
  await p0.getByLabel("Email").fill("owner@example.com");
  await p0.getByLabel("Password").fill("owner password 123");
  await p0.getByRole("button", { name: "Create owner account" }).click();
  await p0.locator(".mono").first().waitFor();
  const secret = (await p0.locator(".mono").first().textContent()).trim();
  await p0.getByRole("button", { name: "Continue to sign in" }).click();
  for (const vp of VPS) for (const th of THEMES) {
    const c = await ctx(vp, th); const p = await c.newPage(); await p.goto(`${BASE}/`); await p.getByLabel("Email").waitFor();
    await shot(p, `${vp}-${th}`, 2, "sign-in"); await c.close();
  }
  await p0.getByLabel("Email").fill("owner@example.com");
  await p0.getByLabel("Password").fill("owner password 123");
  await p0.getByLabel("Authenticator code").fill(totp(secret));
  await p0.getByRole("button", { name: "Sign in" }).click();
  await p0.getByRole("button", { name: "Sign out" }).waitFor();
  const session = await p0.evaluate(() => ({ t: sessionStorage.getItem("goldbot_token"), m: sessionStorage.getItem("goldbot_me") }));
  await c0.close();
  const signedIn = async (vp, th) => {
    const c = await ctx(vp, th);
    await c.addInitScript((s) => { sessionStorage.setItem("goldbot_token", s.t); if (s.m) sessionStorage.setItem("goldbot_me", s.m); }, session);
    return c;
  };

  for (const vp of VPS) for (const th of THEMES) {
    const dir = `${vp}-${th}`;
    const c = await signedIn(vp, th); const pg = await c.newPage(); let n = 3;
    await pg.goto(`${BASE}/`); await pg.locator("[data-nav-item]:visible").first().waitFor();
    for (const s of FOLD) { await pg.goto(`${BASE}/#/${s}`); await pg.reload(); await shot(pg, dir, 0, `fold-${s}`, false); }
    const more = pg.getByRole("button", { name: "More", exact: true });
    if (vp !== "desktop" && await more.count()) {
      await more.click(); await shot(pg, dir, n++, "nav-more-open"); await pg.keyboard.press("Escape");
    }
    for (const s of SECTIONS) {
      await pg.goto(`${BASE}/#/${s}`); await pg.reload(); await pg.waitForTimeout(600);
      await shot(pg, dir, n++, `${s}-default`);
      const tabs = pg.getByRole("tab").filter({ visible: true });
      const count = await tabs.count();
      for (let i = 0; i < count; i++) {
        const tab = tabs.nth(i); const name = slug((await tab.textContent()) ?? `tab-${i}`);
        await tab.click(); await shot(pg, dir, n++, `${s}-${name}`);
      }
    }
    await pg.goto(`${BASE}/#/home`);
    const pill = pg.locator("[data-status-pill]:visible").first();
    if (await pill.count()) { await pill.click(); await shot(pg, dir, n++, "halt-control-open"); }
    await pg.goto(`${BASE}/#/trades`); await pg.reload();
    const reject = pg.getByRole("button", { name: "Reject…" }).filter({ visible: true }).first();
    if (await reject.waitFor({ timeout: 5000 }).then(() => true, () => false)) {
      await reject.click(); await shot(pg, dir, n++, "trades-reject-reasons");
    }
    await c.close();
  }
  for (const vp of VPS) {                                // error state: the API unreachable
    const c = await signedIn(vp, "light"); await c.route("**/api/**", (r) => r.abort());
    const p = await c.newPage(); await p.goto(`${BASE}/`); await p.waitForTimeout(1500);
    await shot(p, `${vp}-light`, 90, "api-unreachable"); await c.close();
  }
  console.log(`\nshots in ${OUT}`);
} finally {
  await browser?.close();
  stop();
}
