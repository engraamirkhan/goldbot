// Screenshot every screen, sub-tab and state at phone (390x844) and Mac (1440x900), light and dark, full page.
//   SHOTS_DIR=~/tmp/ui-audit/after npx playwright test -c playwright.shots.config.ts
// Files: <page>__<tab>__<state>__<device>__<theme>.png plus index.html, a contact sheet to open in one go.
// The backend is the real e2e server (scripts/e2e_server.py); "loading", "error", "empty", "populated" and "halted" are
// the same screens with /api answers replaced (shots/fixtures.ts). Not part of `npm run test:e2e`.
import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { totpClock } from "../e2e/totp";
import * as fx from "./fixtures";

const OUT = process.env.SHOTS_DIR ?? join(process.env.HOME ?? ".", "tmp", "ui-audit", "shots");
const info = () => JSON.parse(readFileSync("shots/.server.json", "utf8")) as { setup_code: string; state_dir: string };
const clock = totpClock(() => info().state_dir);
const OWNER = { email: "owner@example.com", password: "owner password 123" };
const DEVICES = [["phone", 390, 844], ["mac", 1440, 900]] as const;
const THEMES = ["light", "dark"] as const;
const shots: string[] = [];

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "x";

async function snap(page: Page, name: string) {
  for (const [device, w, h] of DEVICES) {
    await page.setViewportSize({ width: w, height: h });
    for (const theme of THEMES) {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.waitForTimeout(150);
      const file = `${name}__${device}__${theme}.png`;
      await page.screenshot({ path: join(OUT, file), fullPage: true, animations: "disabled" });
      shots.push(file);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

type Mode = "real" | "loading" | "error" | "empty" | "populated" | "halted";
let mode: Mode = "real";
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

async function installRoutes(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/auth/") || mode === "real") return route.fallback();
    if (mode === "loading") return;                                   // never answered: the screen stays loading
    if (mode === "error") return route.fulfill(json({ detail: "Internal Server Error" }, 500));
    const populated = mode === "populated" || mode === "halted";
    const halted = mode === "halted";
    const empty: Record<string, unknown> = {
      "/api/accounts": [], "/api/proposals": [], "/api/proposals/recent": [], "/api/agents": [], "/api/feeds": [],
      "/api/agent-runs": [], "/api/jobs": [], "/api/news": [],
      "/api/limits": { ...fx.limits, accounts: [] },
      "/api/tradingview": { ...fx.tradingview, indicators: [], signin: "not_set_up", last_pull_utc: null, live_bars_24h: 0, history_bars: 0 },
    };
    const full: Record<string, unknown> = {
      "/api/accounts": [fx.account(halted)], "/api/proposals": fx.pending(), "/api/proposals/recent": fx.decided,
      "/api/limits": fx.limits, "/api/tradingview": fx.tradingview,
      "/api/status": halted
        ? fx.status({ halted: true, halted_by: "dashboard", halt_reason: "FOMC", drift_halt: true, drift_reasons: ["calibration drift"],
                      blackout: { kind: "calendar", title: "US CPI", accounts: ["icm-demo"], ts_utc: new Date(Date.now() + 25 * 60_000).toISOString(), received_utc: null } })
        : fx.status(),
    };
    const table = populated ? full : empty;
    if (path in table) return route.fulfill(json(table[path]));
    return route.fallback();
  });
}

async function navItems(page: Page): Promise<string[]> {
  const modern = page.locator("[data-nav-item]");
  if (await modern.count()) return [...new Set(await modern.evaluateAll((els) => els.map((e) => e.getAttribute("data-nav-item")!)))];
  return page.locator("header nav button").allTextContents();
}

async function openPage(page: Page, name: string) {
  const modern = page.locator(`[data-nav-item="${name}"]:visible`);
  if (await modern.count()) await modern.first().click();
  else await page.locator("header nav button", { hasText: new RegExp(`^${name}$`) }).click();
}

async function signIn(page: Page, secret: string) {
  const auth = page.getByRole("radio", { name: "Authenticator app", exact: true });
  if (await auth.count()) await auth.check();
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByLabel("Authenticator code").fill(clock.fresh(secret));
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" }).first()).toBeAttached();
}

test("capture every screen", async ({ page }) => {
  test.setTimeout(30 * 60_000);
  mkdirSync(OUT, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  // ------------------------------------------------------------------ sign-in, first run, authenticator
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Create owner account" })).toBeVisible();
  await snap(page, "signin__first-run__default");
  await page.getByLabel("Setup code").fill("wrong-code");
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByRole("button", { name: "Create owner account" }).click();
  await expect(page.locator(".err, [role=alert]").first()).toBeVisible();
  await snap(page, "signin__first-run__error");
  await page.getByLabel("Setup code").fill(info().setup_code);
  await page.getByRole("button", { name: "Create owner account" }).click();
  await expect(page.getByRole("button", { name: "Continue to sign in" })).toBeVisible();
  await snap(page, "signin__add-authenticator__default");
  const secret = (await page.locator(".mono").first().textContent())?.trim() ?? "";
  await page.getByRole("button", { name: "Continue to sign in" }).click();
  await snap(page, "signin__sign-in__default");
  const auth = page.getByRole("radio", { name: "Authenticator app", exact: true });
  if (await auth.count()) {
    await auth.check();
    await snap(page, "signin__sign-in__authenticator");
  }
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByLabel("Authenticator code").fill("000000");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".err, [role=alert]").first()).toBeVisible();
  await snap(page, "signin__sign-in__error");
  for (const link of ["Forgot password?", "Email me a sign-in link", "Use a recovery code"]) {
    const b = page.getByRole("button", { name: link });
    if (!(await b.count())) continue;
    await b.click();
    await snap(page, `signin__${slug(link)}__default`);
    await page.getByRole("button", { name: "Back to sign in" }).click();
  }
  const email = page.getByRole("radio", { name: "Send me a code", exact: true });
  if (await email.count()) {
    await email.check();
    await page.getByLabel("Email").fill(OWNER.email);
    await page.getByLabel("Password").fill(OWNER.password);
    await page.getByRole("button", { name: /send|sign in|continue/i }).first().click();
    await page.waitForTimeout(800);
    await snap(page, "signin__email-code__enter-code");
    await page.goto("/");
  }
  await signIn(page, secret);
  await page.waitForTimeout(1500);

  // ------------------------------------------------------------------ every page, sub-tab and state
  await installRoutes(page);
  const pages = await navItems(page);
  for (const name of pages) {
    const p = slug(name);
    mode = "real";
    await page.reload();
    await expect(page.getByRole("button", { name: "Sign out" }).first()).toBeAttached();
    await openPage(page, name);
    await page.waitForTimeout(1500);
    await snap(page, `${p}__main__live`);
    // sub-tabs
    const tabs = page.locator("main [role=tablist] [role=tab]");
    const tabNames = await tabs.allTextContents();
    for (const t of tabNames.slice(1)) {
      await page.locator("main [role=tablist] [role=tab]", { hasText: t }).first().click();
      await page.waitForTimeout(1200);
      await snap(page, `${p}__${slug(t)}__live`);
    }
    if (tabNames.length) await page.locator("main [role=tablist] [role=tab]").first().click();
    // everything expanded
    const details = page.locator("main details:not([open])");
    if (await details.count()) {
      await details.evaluateAll((els) => els.forEach((e) => ((e as HTMLDetailsElement).open = true)));
      for (const b of await page.locator("main button[aria-expanded=false]").all()) await b.click().catch(() => {});
      await page.waitForTimeout(800);
      await snap(page, `${p}__main__expanded`);
    }
    for (const m of ["loading", "error", "empty", "populated", "halted"] as const) {
      if (m === "halted" && !/home|overview|approvals|trades/.test(p)) continue;
      mode = m;
      await page.reload();
      await expect(page.getByRole("button", { name: "Sign out" }).first()).toBeAttached();
      await openPage(page, name);
      await page.waitForTimeout(m === "loading" ? 600 : 1500);
      await snap(page, `${p}__main__${m}`);
      if (m === "populated") {
        for (const t of tabNames.slice(1)) {
          await page.locator("main [role=tablist] [role=tab]", { hasText: t }).first().click();
          await page.waitForTimeout(1000);
          await snap(page, `${p}__${slug(t)}__populated`);
        }
        if (tabNames.length) await page.locator("main [role=tablist] [role=tab]").first().click();
        const reject = page.getByRole("button", { name: /^Reject/ }).first();
        if (await reject.count() && await reject.isVisible()) {
          await reject.click();
          await snap(page, `${p}__main__reject-reasons`);
        }
      }
    }
    mode = "real";
  }
  writeIndex();
});

function writeIndex() {
  const groups = new Map<string, string[]>();
  for (const f of shots) {
    const key = f.split("__").slice(0, 3).join(" / ");
    groups.set(key, [...(groups.get(key) ?? []), f]);
  }
  const rows = [...groups].map(([k, fs]) => `<section><h2>${k}</h2><div class="row">${fs.map((f) =>
    `<a href="${f}"><figure><img loading="lazy" src="${f}"><figcaption>${f.split("__").slice(3).join(" ").replace(".png", "")}</figcaption></figure></a>`).join("")}</div></section>`);
  writeFileSync(join(OUT, "index.html"), `<!doctype html><meta charset="utf-8"><title>goldbot screens (${shots.length})</title>
<style>body{font:14px system-ui;margin:24px;background:#f4f4f5}h1{font-size:20px}h2{font-size:14px;margin:24px 0 8px}
.row{display:flex;gap:12px;align-items:flex-start;overflow-x:auto}figure{margin:0;background:#fff;padding:6px;border-radius:6px}
img{display:block;width:220px;max-height:520px;object-fit:cover;object-position:top;border:1px solid #ddd}
figcaption{font-size:12px;color:#555;margin-top:4px}</style>
<h1>goldbot screens: ${shots.length} screenshots, ${groups.size} screen states</h1>${rows.join("\n")}`);
}
