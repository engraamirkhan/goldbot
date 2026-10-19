import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { go } from "./nav";
import { totpClock } from "./totp";

const info = () => JSON.parse(readFileSync("e2e/.server.json", "utf8")) as { setup_code: string; state_dir: string };
const clock = totpClock(() => info().state_dir);
/** SHOTS_DIR=<dir> saves full-page screenshots of the screens under test (before/after for design reviews). */
async function shot(page: Page, name: string) {
  if (process.env.SHOTS_DIR) await page.screenshot({ path: join(process.env.SHOTS_DIR, `${name}.png`), fullPage: true });
}
const OWNER = { email: "owner@example.com", password: "owner password 123" };
const VIEWER = { email: "viewer@example.com", password: "viewer password 123" };

async function enrolledSecret(page: Page): Promise<string> {
  const secret = (await page.locator(".mono").first().textContent())?.trim() ?? "";
  expect(secret).toMatch(/^[A-Z2-7]+$/);
  await page.getByRole("button", { name: "Continue to sign in" }).click();
  return secret;
}

async function signIn(page: Page, email: string, password: string, secret: string) {
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByLabel("Authenticator code").fill(clock.fresh(secret));
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

/** The header names the role, never the address (docs/ux/header-role.md). The local part "owner" equals the role
 *  word, so the identity check is the whole address, its domain and the "@". */
async function expectRoleNotEmail(page: Page, role: string, email: string) {
  const banner = page.getByRole("banner");
  await expect(banner).toContainText(`Signed in as ${role}`);
  await expect(banner).not.toContainText(email);
  await expect(banner).not.toContainText(email.split("@")[1]);
  await expect(banner).not.toContainText("@");
  expect(await banner.innerHTML()).not.toContain("@");                         // no title / aria-label carries it
}

// One serial story: first run -> owner approves a trade -> halts and re-arms -> invites a viewer -> viewer sees but cannot act.
// each authenticator code is accepted once, so a test that needs another one steps the server's TOTP clock (totpClock)
test.describe.configure({ mode: "serial", timeout: 90_000 });
let ownerSecret = "";
let inviteLink = "";
let viewerSecret = "";

test("first run creates the owner with an authenticator, then signs in", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Create owner account" })).toBeVisible();
  await page.getByLabel("Setup code").fill("wrong-code");
  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByRole("button", { name: "Create owner account" }).click();
  await expect(page.locator(".err")).toContainText("setup not available");

  await page.getByLabel("Setup code").fill(info().setup_code);
  await page.getByRole("button", { name: "Create owner account" }).click();
  ownerSecret = await enrolledSecret(page);

  await page.getByLabel("Email").fill(OWNER.email);
  await page.getByLabel("Password").fill(OWNER.password);
  await page.getByLabel("Authenticator code").fill("000000");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".err")).toContainText("invalid credentials");

  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await expectRoleNotEmail(page, "Owner", OWNER.email);
  expect(await page.title()).not.toContain("@");
  await go(page, "Approvals");
  await expect(page.getByRole("region", { name: "Safety state" })).not.toContainText("@");   // persistent chrome
  await go(page, "Account");
  await expect(page.getByRole("region", { name: "Your sign-in" })).toContainText(OWNER.email);   // the one place it shows
});

test("owner approves one proposal and rejects the other with a reason", async ({ page }) => {
  await page.goto("/");
  const live = page.waitForEvent("websocket");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  // the live feed must actually upgrade (needs uvicorn[standard]); a rejected upgrade closes it at once
  const ws = await live;
  expect(ws.url()).toMatch(/\/ws$/);
  await page.waitForTimeout(500);
  expect(ws.isClosed()).toBe(false);
  await go(page, "Approvals");
  await expect(page.getByText("No engines reporting")).toBeVisible();         // safety strip above the cards
  const waiting = page.getByRole("region", { name: "Waiting for your decision" }).locator("article.card");
  await expect(waiting).toHaveCount(2);
  const long = waiting.and(page.locator(".long"));
  await expect(long.locator(".dir")).toHaveText(/LONG/);
  await expect(long.getByText("£8.20", { exact: true })).toBeVisible();         // risk in the account currency
  await expect(long.getByText("(0.82% of equity)")).toBeVisible();
  await expect(long.getByText("Smallest trade size: above the normal 0.5%, within your 1% cap.")).toBeVisible();
  await expect(long.getByText("Loss budget left today: unknown")).toBeVisible();   // no engine reporting yet
  await expect(long.getByText("−£8.20")).toBeVisible();                         // the stop in money
  const short0 = waiting.and(page.locator(".short"));
  await expect(short0.getByText("$20.00", { exact: true })).toBeVisible();      // an older engine: dollars, labelled
  await expect(short0.getByText("(USD)")).toBeVisible();
  await page.setViewportSize({ width: 360, height: 800 });
  const lbox = await long.boundingBox();
  expect(lbox && lbox.x >= 0 && lbox.x + lbox.width <= 360).toBe(true);        // the card fits the phone

  await long.getByRole("button", { name: "Approve" }).click();
  await expect(waiting).toHaveCount(1);
  const decided = page.getByRole("region", { name: "Decided in the last 10 minutes" });
  await page.getByRole("tab", { name: /^Recent/ }).click();                    // Trades: Waiting | Recent
  await expect(decided.getByText("Approval sent.")).toBeVisible();             // the engine applies it on its next tick
  await page.getByRole("tab", { name: /^Waiting/ }).click();
  const short = waiting.and(page.locator(".short"));
  await short.getByRole("button", { name: "Reject…" }).click();
  await short.getByRole("group", { name: "Reason for rejecting" }).getByRole("button", { name: "Cost" }).click();
  await expect(page.getByText(/No proposals waiting/)).toBeVisible();
  await page.getByRole("tab", { name: /^Recent/ }).click();
  await expect(decided.getByText(/Rejected · Cost/)).toBeVisible();
});

test("owner halts new entries and re-arms with an authenticator code", async ({ page }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await page.locator("[data-status-pill]:visible").click();                   // Halt: two taps from any screen
  await page.getByLabel("Halt reason").fill("fomc surprise");
  await page.getByRole("button", { name: "Halt new entries" }).click();
  await expect(page.getByText("Entries halted via the dashboard: fomc surprise.")).toBeVisible();   // the channel, never who (A15)
  await page.getByLabel("Authenticator code").fill("000000");
  await page.getByRole("button", { name: "Re-arm" }).click();
  await expect(page.locator(".err")).toContainText("authenticator code required");
  await page.getByLabel("Authenticator code").fill(clock.fresh(ownerSecret));
  await page.getByRole("button", { name: "Re-arm" }).click();
  await expect(page.getByRole("button", { name: "Halt new entries" })).toBeVisible();
});

test("owner enables auto mode with an authenticator code on the evidence, then switches back to propose", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });                     // phone first
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await go(page, "Entry mode");
  const card = page.getByRole("region", { name: /Entry mode/ });
  await expect(card.getByRole("heading", { name: /Entry mode: Propose-and-approve/ })).toBeVisible();
  await expect(card.getByText("auto mode offered")).toBeVisible();             // 100 seeded decided proposals, no difference
  const box = await card.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 360).toBe(true);           // fits the phone without sideways scroll
  await card.getByLabel("Code to enable auto mode").fill("000000");
  await card.getByRole("button", { name: "Enable auto mode" }).click();
  await expect(card.getByRole("alert")).toContainText("authenticator code required");
  await card.getByLabel("Code to enable auto mode").fill(clock.fresh(ownerSecret));
  await card.getByRole("button", { name: "Enable auto mode" }).click();
  await expect(card.getByRole("heading", { name: /Entry mode: Auto/ })).toBeVisible();
  await expect(card.getByRole("status")).toContainText("Mode: AUTO");
  await card.getByRole("button", { name: "Switch to propose" }).click();
  await expect(card.getByRole("heading", { name: /Entry mode: Propose-and-approve/ })).toBeVisible();
  await expect(card.getByText(/Set .* via the dashboard \(owner\)/)).toBeVisible();   // channel and role, never who (A15)
});

test("owner regenerates the recovery codes with password and authenticator code; they are shown once", async ({ page }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await go(page, "Account");
  await page.getByLabel("Your password").fill(OWNER.password);
  await page.getByLabel("Code from your authenticator app").fill(clock.fresh(ownerSecret));
  const generate = page.getByRole("button", { name: "Generate new recovery codes" });
  await expect(generate).toBeDisabled();
  await page.getByLabel(/My current recovery codes will stop working/).check();
  await generate.click();
  await expect(page.getByRole("heading", { name: "Your new recovery codes" })).toBeVisible();
  await expect(page.locator("ol.codes li")).toHaveCount(10);
  await page.getByRole("button", { name: "I have saved them" }).click();
  await expect(page.locator("ol.codes")).toHaveCount(0);
});

test("news tab shows the calendar with blackout windows and the scored headlines", async ({ page }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await go(page, "News");
  await expect(page.getByRole("heading", { name: "Economic calendar" })).toBeVisible();
  const cpi = page.locator("tr", { hasText: "CPI m/m" });
  await expect(cpi.locator(".badge.tier1")).toHaveText("T1");
  await expect(cpi.locator("td").last()).toHaveText(/^\d\d:\d\d–\d\d:\d\d/);   // the seeded tier-1 event's blackout window
  await expect(page.locator("tr", { hasText: "ISM Manufacturing PMI" }).locator("td").last()).toHaveText("—");
  const shock = page.locator("tr.shock", { hasText: "Missile strike" });
  await expect(shock.getByText("SHOCK")).toBeVisible();
  await expect(shock.getByText("risk-off")).toBeVisible();
  await expect(page.locator("tr", { hasText: "Weekend football results" }).getByText("unscored")).toBeVisible();
  await page.getByLabel("Minimum relevance").selectOption("0.7");
  await expect(page.getByText("Weekend football results")).toHaveCount(0);
  await expect(page.getByText("Fed's Powell signals a pause in hikes")).toBeVisible();
});

test("health tab shows agent halts, PSI bands and the charts; research tab the budget, trials and hypotheses", async ({ page }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await go(page, "Health");
  const agents = page.locator("table.agents-health");
  await expect(agents.locator("tr", { hasText: "tsmom-g0" })).toContainText("50%: sized down");
  await expect(agents.locator("tr", { hasText: "trend-g1" })).toContainText(/Halted since/);
  await expect(page.getByTitle(/tsmom-g0 · atr_14: PSI 0.310 \(sized down\)/)).toHaveClass(/bad/);
  await expect(page.getByRole("img", { name: /Reliability of tsmom-g0/ })).toBeVisible();
  await expect(page.getByRole("img", { name: /CUSUM of tsmom-g0 over 8 trades/ })).toBeVisible();
  await expect(page.getByText(/^Fixed threshold \(not calibrated\)|^Threshold h .* calibrated/)).toBeVisible();
  await expect(page.getByRole("meter", { name: "trend-g1 30-day drawdown" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Health checks" })).toContainText("Deploy");
  await expect(page.getByRole("alert")).toHaveCount(0);                       // no system halt seeded

  await go(page, "Research");
  await expect(page.getByRole("meter", { name: /trials used/ })).toBeVisible();
  const trials = page.locator("table.trials");
  await expect(trials.locator("tbody tr")).toHaveCount(2);
  await expect(trials.locator("tbody tr").first()).toContainText("+0.061 (t 2.63)");
  await expect(trials.getByText("failed: dsr").first()).toBeVisible();
  await expect(page.getByText("gross t 2.63 but net negative")).toBeVisible();
  await expect(page.getByText("4 trials reserved")).toBeVisible();
  await expect(page.getByRole("list", { name: "Retired families" }).getByRole("listitem").first()).toContainText("1 trial this quarter");
  await expect(page.locator("table.moves tbody tr").first()).toContainText("11 → 12");
  await expect(page.getByRole("note")).toContainText("hypotheses.md changed since this plan");
  await expect(page.locator("details.hyp").first()).toContainText("H-01");    // the real docs/research/hypotheses.md
});

test("owner invites a viewer", async ({ page }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await go(page, "Users");
  await page.getByPlaceholder("email to invite").fill(VIEWER.email);
  await page.getByRole("button", { name: "Create invite link" }).click();
  inviteLink = (await page.locator("code.mono").textContent()) ?? "";
  expect(inviteLink).toContain("/#invite=");
});

test("viewer accepts the invite and can watch but not approve or manage users", async ({ page }) => {
  await page.goto(inviteLink);
  await expect(page.getByRole("button", { name: "Accept invite" })).toBeVisible();
  await page.getByLabel("Password").fill("short");
  await page.getByRole("button", { name: "Accept invite" }).click();
  await expect(page.locator(".err")).toBeVisible();
  await page.getByLabel("Password").fill(VIEWER.password);
  await page.getByRole("button", { name: "Accept invite" }).click();
  const secret = await enrolledSecret(page);
  viewerSecret = secret;

  await signIn(page, VIEWER.email, VIEWER.password, secret);
  await expectRoleNotEmail(page, "Viewer", VIEWER.email);
  await go(page, "Settings");
  await expect(page.getByRole("tab", { name: "Users" })).toHaveCount(0);
  for (const tab of ["Overview", "News", "Agents", "Health", "Research", "Feeds", "Approvals"]) {
    await go(page, tab);
  }
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);

  // Progress (docs/ux/progress-screens.md) is read-only for every role: on a fresh state nothing is recorded, the stop
  // rule has not started and the daily results job has not run; nobody sees a record-gate command here
  await page.setViewportSize({ width: 360, height: 800 });
  await go(page, "Progress");
  await expect(page.getByRole("heading", { name: "Road to live" })).toBeVisible();
  await expect(page.getByTestId("gate-foundation_to_backtest")).toContainText("Not met");
  await expect(page.getByTestId("stop-rule")).toContainText("Not started");
  await expect(page.getByText(/No results yet/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy" })).toHaveCount(0);
  const road = await page.getByRole("region", { name: "Road to live" }).boundingBox();
  expect(road && road.x >= 0 && road.x + road.width <= 360).toBe(true);        // fits the phone without sideways scroll
  await page.setViewportSize({ width: 1280, height: 720 });

  // the API enforces the same rule the UI hides
  const token = await page.evaluate(() => sessionStorage.getItem("goldbot_token"));
  const r = await page.request.post("/api/decisions", {
    data: { proposal_id: "e2e-long", action: "approve", created: 0 }, headers: { authorization: `Bearer ${token}` },
  });
  expect(r.status()).toBe(403);
  const mode = await page.request.post("/api/automode", { data: { mode: "propose" }, headers: { authorization: `Bearer ${token}` } });
  expect(mode.status()).toBe(403);                                            // the mode switch is owner-only
});

test("viewer resets a forgotten password with an authenticator code, then changes it while signed in", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Forgot password" }).click();
  await page.getByLabel("Email").fill(VIEWER.email);
  await page.getByLabel("New password").fill("forgotten viewer pw 1");
  await page.getByLabel("Authenticator code").fill("000000");
  await page.getByRole("button", { name: "Set new password" }).click();
  await expect(page.locator(".err")).toContainText("invalid credentials");
  await page.getByLabel("Authenticator code").fill(clock.fresh(viewerSecret));
  await page.getByRole("button", { name: "Set new password" }).click();
  await expect(page.getByText("Password changed. Sign in with the new password.")).toBeVisible();
  await signIn(page, VIEWER.email, "forgotten viewer pw 1", viewerSecret);

  await go(page, "Account");
  await page.getByLabel("Current password").fill("forgotten viewer pw 1");
  await page.getByLabel("Authenticator code").fill(clock.fresh(viewerSecret));
  await page.getByLabel("New password (12+ characters)").fill(VIEWER.password);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText("Password changed. Your other sessions were signed out.")).toBeVisible();
});

test("owner manages a user's access and never offers the owner role", async ({ page }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await go(page, "Users");
  await expect(page.locator("form.inline option[value=owner]")).toHaveCount(0);
  const row = page.locator("tr", { hasText: VIEWER.email });
  await row.getByRole("button", { name: "Reset link" }).click();
  await expect(page.locator("code.mono")).toContainText("/#reset=");
  await row.getByRole("button", { name: "Disable" }).click();
  await expect(row.getByRole("button", { name: "Enable" })).toBeVisible();
  await row.getByRole("button", { name: "Enable" }).click();
  await expect(row.getByRole("button", { name: "Disable" })).toBeVisible();
});

// last in the story: an engine starts reporting (earlier steps rely on "No engines reporting")
test("once the engine reports, Trades > Summary shows the live balance, today's loss budget and the next milestone on a phone", async ({ page }) => {
  const dir = info().state_dir;
  writeFileSync(join(dir, "engine_icm-demo.json"), JSON.stringify({
    account: "icm-demo", broker: "icm", mode: "demo", equity: 1490, balance: 1500, currency: "GBP", margin: 0,
    day_start_equity: 1500, week_start_equity: 1500, balance_closed_hwm: 1500, stage: "normal", open_positions: 0,
    account_class: "raw", ts: Date.now() / 1000, price: 4150, spread_points: 20, usd_to_account: 1 / 1.33,
    limits: { risk_per_trade: 0.005, max_risk_per_trade: 0.01, multiplier_bounds: [0.25, 1.5], daily_cap: 0.02, weekly_cap: 0.05 },
    ladder_atr: { "15m": 7.27, "1h": 14.6, "4h": 29.0, "1d": 70.0 },
    lot_terms: { contract_oz: 100, volume_min: 0.01, volume_step: 0.01, volume_max: 50, stops_level_points: 0 },
    entry_blocks: [], entry_refusals: {},                                        // the gate refuses nothing (BACKLOG 43)
  }));
  writeFileSync(join(dir, "supervisor.json"), JSON.stringify({
    ts: Date.now() / 1000, halt: false, reasons: [], daily_cap: 0.015, combined_day_start_equity: 1500, weekly_cap: 0.04,
    combined_week_start_equity: 1500, combined_equity: 1490,
  }));
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await go(page, "Trades summary");                                            // the balance card moved off Home (home v2)
  const card = page.getByRole("region", { name: "Account icm-demo" });
  await expect(card.getByText("£1,500.00")).toBeVisible();                     // balance from the broker, in pounds
  await expect(card.getByText("£12.50 left")).toBeVisible();                   // 1.5% of £1,500 less £10 lost today
  await expect(card.getByRole("meter", { name: "Daily loss budget used" })).toBeVisible();
  await expect(card.getByText(/Next: 1-hour trades at £2,7/)).toBeVisible();
  await expect(card.getByRole("progressbar", { name: "Progress to 1-hour trades" })).toBeVisible();
  const box = await card.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 360).toBe(true);           // fits the phone
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);   // no sideways scroll
  await go(page, "Approvals");
  await expect(page.getByText("Loss budget", { exact: true })).toBeVisible();   // safety strip chip
});

test("entry status says in plain words why an account can't trade, when it resumes and that exits keep running", async ({ page }) => {
  const dir = info().state_dir;
  const engine = JSON.parse(readFileSync(join(dir, "engine_icm-demo.json"), "utf8"));
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  // written after sign-in (which may wait for a fresh authenticator code): a report older than the supervisor's
  // 60 s heartbeat, or stamped in the future, reads as "not reporting"
  const until = new Date(Date.now() + 90 * 60_000).toISOString();
  writeFileSync(join(dir, "engine_icm-demo.json"), JSON.stringify({
    ...engine, ts: Date.now() / 1000, market_closed: true, sessions: { server_tz: "Europe/Athens", source: "static" },
    server_clock: { status: "mismatch", reason: "server clock reads UTC+3" }, login: 99887766,
    entry_blocks: ["server_clock_mismatch", "market_closed"],                    // the gate's reasons (BACKLOG 43)
    throttle: { enabled: true, loss_streak: 3, max_entries_per_day_per_agent: 3, agents: {
      "trend-g0": { timeframe: "1h", loss_streak: 3, cooldown_until: until, blocked: ["throttle_loss_streak"] } } },
  }));
  const row = page.getByRole("article", { name: "Entry status icm-demo" });
  await expect(row.getByText("Blocked", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(row.getByText("No new trades: Broker clock differs from the expected time zone (+2 more)")).toBeVisible();
  await expect(row.getByText("Needs you")).toBeVisible();
  await expect(row.getByRole("link", { name: "Runbook: Server clock (US-EU DST gap weeks)" })).toHaveAttribute(
    "href", /RUNBOOK\.md#server-clock-us-eu-dst-gap-weeks$/);
  await expect(row.getByText(/^opens .* your time \(in /)).toHaveAttribute("title", / UTC$/);
  await expect(row.getByText("Trend (trend-g0) paused after 3 losses in a row")).toBeVisible();
  await expect(row.getByText(/^until .* your time \(in 1 h \d+ min\)$/)).toBeVisible();
  await expect(row.getByText(/^Exits keep running/)).toBeVisible();
  await expect(row).not.toContainText("99887766");                            // no account identifiers
  const box = await row.boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 360).toBe(true);            // fits the phone
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  const link = await row.getByRole("link").first().boundingBox();
  expect(link && link.height >= 44).toBe(true);                                // a thumb-sized tap target
  await go(page, "Approvals");                // the strip agrees with the panel
  await expect(page.getByRole("status").filter({ hasText: "New entries blocked" })).toContainText("Market closed");
});

// Home v2 (docs/ux/home-v2-2026-10-19.md, owner 2026-10-19): a calm status page of five cards in order at 360 and
// 1080 px, light and dark, and everything about trades on the Trades screen (Open, Activity, Summary). Today's money
// carries its DEMO badge and names the limit that binds; Home links to the open trades; nothing scrolls sideways.
async function noSideways(page: Page, width: number) {
  const wide = await page.evaluate((w) => Array.from(document.querySelectorAll("body *")).filter((e) => {
    if (e.getBoundingClientRect().right <= w + 1) return false;
    for (let p = e.parentElement; p; p = p.parentElement) if (getComputedStyle(p).overflowX === "auto") return false;   // scrolls in its own box
    return true;
  }).slice(0, 5).map((e) => `${e.tagName}.${e.className}`), width);
  expect(wide).toEqual([]);
}

test("Home is five calm cards and the trades live on Trades, on a phone and a desktop, light and dark", async ({ page }) => {
  const dir = info().state_dir;
  const engine = JSON.parse(readFileSync(join(dir, "engine_icm-demo.json"), "utf8"));
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  writeFileSync(join(dir, "engine_icm-demo.json"), JSON.stringify({
    ...engine, ts: Date.now() / 1000, market_closed: false, entry_blocks: [], throttle: undefined, server_clock: undefined,
    daily_loss_limit: 50, daily_loss_remaining: 40,
  }));
  const home = ["Needs you", "System status", "Today", "Your strategies", "Learning"];
  for (const scheme of ["light", "dark"] as const) {
    for (const width of [360, 1080]) {
      await page.emulateMedia({ colorScheme: scheme });
      await page.setViewportSize({ width, height: 900 });
      await go(page, "Overview");
      await page.reload();
      for (const name of home) await expect(page.getByRole("region", { name })).toBeVisible();
      for (const name of ["Activity feed", "Trades summary", "Open trades", "MT5 chart"]) await expect(page.getByRole("region", { name })).toHaveCount(0);
      await expect(page.getByRole("heading", { name: "Nothing needs you" })).toBeVisible({ timeout: 15_000 });
      await expect(page.getByRole("region", { name: "System status" })).toContainText("Engine running · 1 account");
      const today = page.getByRole("group", { name: "Today icm-demo" });
      await expect(today.getByText("DEMO", { exact: true })).toBeVisible({ timeout: 15_000 });
      // config/accounts.yaml runs cost probes on icm-demo (ADR 0037 amendment 3): £10 lost, the ~£16 probe stop binds
      // (15 s: the API's 5 s cache may still hold the report from before the write, and the card polls every 5 s)
      await expect(today.getByText("£6.00 left before the probe stop (£16.00)")).toBeVisible({ timeout: 15_000 });
      if (width === 360) {
        const tops = await Promise.all(home.map(async (name) => (await page.getByRole("region", { name }).boundingBox())!.y));
        expect([...tops].sort((a, b) => a - b)).toEqual(tops);                   // one column, in the decided order
      }
      await noSideways(page, width);
      await shot(page, `home-${width}-${scheme}`);

      await page.getByRole("button", { name: /open trades?( · see trades)? →$/ }).click();   // Home -> Trades > Open
      await expect(page.getByRole("tab", { name: "Open", exact: true })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("region", { name: "Open trades" })).toBeVisible();
      await expect(page.getByRole("region", { name: "Safety state" })).toBeVisible();
      await noSideways(page, width);
      await shot(page, `trades-open-${width}-${scheme}`);
      await go(page, "Trades activity");
      await expect(page.getByRole("region", { name: "Activity feed" })).toBeVisible();
      await noSideways(page, width);
      await shot(page, `trades-activity-${width}-${scheme}`);
      await go(page, "Trades summary");
      for (const name of ["Today", "Account icm-demo", "Trades summary"]) await expect(page.getByRole("region", { name })).toBeVisible();
      await noSideways(page, width);
      await shot(page, `trades-summary-${width}-${scheme}`);
    }
  }
});
