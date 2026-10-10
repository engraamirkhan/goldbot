import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { totp } from "./totp";

const info = () => JSON.parse(readFileSync("e2e/.server.json", "utf8")) as { setup_code: string };
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
  await page.getByLabel("Authenticator code").fill(totp(secret));
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

// One serial story: first run -> owner approves a trade -> halts and re-arms -> invites a viewer -> viewer sees but cannot act.
test.describe.configure({ mode: "serial" });
let ownerSecret = "";
let inviteLink = "";

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
  await expect(page.getByText(`${OWNER.email} · owner`)).toBeVisible();
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
  await page.getByRole("button", { name: "Approvals" }).click();
  await expect(page.getByText("No engines reporting")).toBeVisible();         // safety strip above the cards
  const waiting = page.getByRole("region", { name: "Waiting for your decision" }).locator("article.card");
  await expect(waiting).toHaveCount(2);
  const long = waiting.and(page.locator(".long"));
  await expect(long.locator(".dir")).toHaveText(/LONG/);
  await expect(long.getByText("$20")).toBeVisible();                           // $ risk from the engine

  await long.getByRole("button", { name: "Approve" }).click();
  await expect(waiting).toHaveCount(1);
  const decided = page.getByRole("region", { name: "Decided in the last 10 minutes" });
  await expect(decided.getByText("Approval sent.")).toBeVisible();             // the engine applies it on its next tick
  const short = waiting.and(page.locator(".short"));
  await short.getByRole("button", { name: "Reject…" }).click();
  await short.getByRole("group", { name: "Reason for rejecting" }).getByRole("button", { name: "Cost" }).click();
  await expect(decided.getByText(/Rejected · Cost/)).toBeVisible();
  await expect(page.getByText(/No proposals waiting/)).toBeVisible();
});

test("owner halts new entries and re-arms with an authenticator code", async ({ page }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await page.getByLabel("Halt reason").fill("fomc surprise");
  await page.getByRole("button", { name: "Halt new entries" }).click();
  await expect(page.getByText(`Entries halted by dashboard:${OWNER.email}: fomc surprise.`)).toBeVisible();
  await page.getByLabel("Authenticator code").fill("000000");
  await page.getByRole("button", { name: "Re-arm" }).click();
  await expect(page.locator(".err")).toContainText("authenticator code required");
  await page.getByLabel("Authenticator code").fill(totp(ownerSecret));
  await page.getByRole("button", { name: "Re-arm" }).click();
  await expect(page.getByRole("button", { name: "Halt new entries" })).toBeVisible();
});

test("news tab shows the calendar with blackout windows and the scored headlines", async ({ page }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await page.getByRole("button", { name: "News", exact: true }).click();
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

test("owner invites a viewer", async ({ page }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await page.getByRole("button", { name: "Users" }).click();
  await page.getByPlaceholder("email to invite").fill(VIEWER.email);
  await page.getByRole("button", { name: "Create invite link" }).click();
  inviteLink = (await page.locator("code.mono").textContent()) ?? "";
  expect(inviteLink).toContain("/?invite=");
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

  await signIn(page, VIEWER.email, VIEWER.password, secret);
  await expect(page.getByText(`${VIEWER.email} · viewer`)).toBeVisible();
  await expect(page.getByRole("button", { name: "Users" })).toHaveCount(0);
  for (const tab of ["Overview", "News", "Agents", "Feeds", "Approvals"]) {
    await page.getByRole("button", { name: tab, exact: true }).click();
  }
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);

  // the API enforces the same rule the UI hides
  const token = await page.evaluate(() => sessionStorage.getItem("goldbot_token"));
  const r = await page.request.post("/api/decisions", {
    data: { proposal_id: "e2e-long", action: "approve" }, headers: { authorization: `Bearer ${token}` },
  });
  expect(r.status()).toBe(403);
});
