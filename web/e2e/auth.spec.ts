import { expect, test, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { go } from "./nav";
import { totp, totpClock } from "./totp";

// The sign-in story on its own server (playwright.config.ts project "auth"), whose emails are captured by the
// e2e-only fake mailer (goldbot/api/e2e_mailer.py) into a JSONL outbox: login, forgot-password, the owner's password
// reset link for another user, the owner's emailed sign-in link, the neutral answer for unknown accounts, and a phone.
type Server = { setup_code: string; state_dir: string; outbox: string | null };
type Mail = { to: string; subject: string; body: string };
const server = () => JSON.parse(readFileSync("e2e/.server-auth.json", "utf8")) as Server;
const clock = totpClock(() => server().state_dir);
const OWNER = { email: "owner@example.com", password: "owner password 123" };
const VIEWER = { email: "viewer@example.com", password: "viewer password 123" };
const UNKNOWN = "nobody@example.com";
const NEUTRAL = "If this address can use email sign-in, a link is on its way";
const LINK_SUBJECT = "Your goldbot sign-in link";
const NOTICE_SUBJECT = "goldbot: new sign-in with an emailed link";
const CODE_SUBJECT = "Your goldbot sign-in code";

function mails(): Mail[] {
  const p = server().outbox;
  expect(p, "the auth server runs with --fake-mailer").toBeTruthy();
  if (!p || !existsSync(p)) return [];
  return readFileSync(p, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as Mail);
}

/** The first message with this subject after the first `seen` messages (mail goes out after the answer, so poll). */
async function mailAfter(seen: number, subject: string): Promise<Mail> {
  await expect.poll(() => mails().slice(seen).some((m) => m.subject === subject), {
    timeout: 10_000, message: `no "${subject}" mail: not sent, or the owner address hit its 3 links an hour`,
  }).toBe(true);
  return mails().slice(seen).find((m) => m.subject === subject)!;
}

function signInLink(m: Mail, baseURL: string): string {
  const link = m.body.match(/https?:\/\/\S+\/#signin=[A-Za-z0-9_-]+/)?.[0] ?? "";
  expect(link.startsWith(`${baseURL}/#signin=`)).toBe(true);
  return link;
}

/** Each test its own client address (TEST-NET-3), read by the API from cf-connecting-ip on a loopback peer, so the
 * per-IP hourly limit on sign-in-link requests (3) and the failure lockout count per test, not per run. */
async function asClient(page: Page, ip: string) {
  await page.setExtraHTTPHeaders({ "cf-connecting-ip": ip });
}

/** Open a link from an email as a fresh page load (a mail client opens a new tab): a bare `goto` from "/" to
 * "/#signin=..." would only change the fragment of the page already loaded, and the app reads tokens at load. */
async function openFromMail(page: Page, link: string) {
  await page.goto("about:blank");
  await page.goto(link);
}

/** Every visible field and button of a sign-in screen fits 360 px with a thumb-sized (44 px) target; the authenticator
 * field kept hidden while the email code is chosen (its reserved slot) is not a target. */
async function fitsThePhone(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360); // no sideways scroll
  for (const el of await page.locator("form.login input:visible, form.login button:visible").all()) {
    const box = await el.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= 360).toBe(true);
    expect(box && box.height >= 44).toBe(true);                                // thumb-sized tap targets
  }
}

async function enrolledSecret(page: Page): Promise<string> {
  await expect(page.getByRole("heading", { name: "Add goldbot to your authenticator", exact: true })).toBeVisible();
  const secret = (await page.locator("p.mono").first().textContent())?.trim() ?? "";
  expect(secret).toMatch(/^[A-Z2-7]+$/);
  await page.getByRole("button", { name: "Continue to sign in", exact: true }).click();
  return secret;
}

/** The auth server can send mail, so once the owner exists the Sign in screen defaults to "Send me a code"
 * (docs/ux/sign-in-email-code.md State A); these steps use today's path. */
async function chooseAuthenticator(page: Page) {
  await page.getByRole("radio", { name: "Authenticator app", exact: true }).check();
}

async function signIn(page: Page, email: string, password: string, secret: string) {
  await chooseAuthenticator(page);
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByLabel("Authenticator code", { exact: true }).fill(clock.fresh(secret));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
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

// each authenticator code is accepted once, so a test that needs another one steps the server's TOTP clock (totpClock)
// rather than sleeping for the next real 30 s step.
// No retries: a retry would rerun the story against the same server state, where first-run setup is spent and the
// owner address (one per server, by design) keeps its hourly link count, so a rate-limited retry would fail as a
// missing mail instead of the real fault. The story asks for two owner links and one owner code: the shared limit of
// 3 mails an hour per address (ADR 0028 ruling 5) is reached exactly, so a fourth owner mail would be refused.
test.describe.configure({ mode: "serial", timeout: 90_000, retries: 0 });
let ownerSecret = "";
let viewerSecret = "";
let inviteLink = "";
let ownerAnswer = { status: 0, body: "" };          // the API's answer to the owner's link request

test("login: the owner is created, a wrong code is refused, the right one signs in and out", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Setup code", { exact: true }).fill(server().setup_code);
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByRole("button", { name: "Create owner account", exact: true }).click();
  ownerSecret = await enrolledSecret(page);

  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByRole("radio", { name: "Send me a code", exact: true })).toBeChecked();   // the owner exists now
  await chooseAuthenticator(page);
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill("not the password 1");
  await page.getByLabel("Authenticator code", { exact: true }).fill(clock.wrong(ownerSecret));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("invalid credentials");
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  await page.getByLabel("Authenticator code", { exact: true }).fill(clock.wrong(ownerSecret));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("invalid credentials");   // the same words: nothing to tell apart
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await expectRoleNotEmail(page, "Owner", OWNER.email);
  expect(await page.evaluate(() => sessionStorage.getItem("goldbot_token"))).toBeTruthy();

  // a second account for the later steps
  await go(page, "Users");
  await page.getByPlaceholder("email to invite").fill(VIEWER.email);
  await page.getByRole("button", { name: "Create invite link", exact: true }).click();
  inviteLink = (await page.locator("code.mono").textContent()) ?? "";
  expect(inviteLink).toContain("/#invite=");

  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("goldbot_token"))).toBeNull();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();   // signed out stays out
});

test("forgot password: an unknown account is answered like a wrong code; the right code resets it, no email", async ({ page }) => {
  await page.goto(inviteLink);
  await page.getByLabel("New password", { exact: true }).fill(VIEWER.password);
  await page.getByRole("button", { name: "Accept invite", exact: true }).click();
  viewerSecret = await enrolledSecret(page);
  const before = mails().length;

  await page.getByRole("button", { name: "Forgot password?", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Reset your password", exact: true })).toBeVisible();
  await page.getByLabel("Email", { exact: true }).fill(UNKNOWN);
  await page.getByLabel("New password", { exact: true }).fill("a new password 42");
  await page.getByLabel("Authenticator code", { exact: true }).fill(totp(viewerSecret));   // a code valid somewhere
  await page.getByRole("button", { name: "Set new password", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("invalid credentials");
  const unknownAnswer = await page.getByRole("alert").textContent();
  await page.getByLabel("Email", { exact: true }).fill(VIEWER.email);
  await page.getByLabel("Authenticator code", { exact: true }).fill(clock.wrong(viewerSecret));
  await page.getByRole("button", { name: "Set new password", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(unknownAnswer ?? "");

  await page.getByLabel("Authenticator code", { exact: true }).fill(clock.fresh(viewerSecret));
  await page.getByRole("button", { name: "Set new password", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Password changed. Sign in with the new password.");
  await signIn(page, VIEWER.email, "a new password 42", viewerSecret);
  await expectRoleNotEmail(page, "Viewer", VIEWER.email);
  expect(mails().length).toBe(before);                                         // self-service reset: nothing mailed
});

test("password reset link: the owner's link sets a new password and authenticator, and works once", async ({ page, browser }) => {
  await page.goto("/");
  await signIn(page, OWNER.email, OWNER.password, ownerSecret);
  await go(page, "Users");
  await page.locator("tr", { hasText: VIEWER.email }).getByRole("button", { name: "Reset link", exact: true }).click();
  const resetLink = (await page.locator("code.mono").textContent()) ?? "";
  expect(resetLink).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/#reset=[A-Za-z0-9_-]+$/);

  const viewer = await browser.newPage();                                      // the viewer, on their own browser
  await viewer.goto(resetLink);
  await expect(viewer.getByRole("heading", { name: "Set a new password", exact: true })).toBeVisible();
  expect(new URL(viewer.url()).hash).toBe("");                                 // the token leaves the address bar
  await viewer.getByLabel("New password", { exact: true }).fill("short");
  await viewer.getByRole("button", { name: "Set password and authenticator", exact: true }).click();
  await expect(viewer.getByRole("alert")).toBeVisible();                       // the policy still applies
  await viewer.getByLabel("New password", { exact: true }).fill("viewer reset pw 777");
  await viewer.getByRole("button", { name: "Set password and authenticator", exact: true }).click();
  const newSecret = await enrolledSecret(viewer);
  expect(newSecret).not.toBe(viewerSecret);                                    // a new authenticator, too
  await signIn(viewer, VIEWER.email, "viewer reset pw 777", newSecret);
  await expectRoleNotEmail(viewer, "Viewer", VIEWER.email);
  viewerSecret = newSecret;
  await viewer.close();

  const again = await browser.newPage();                                       // the same link a second time
  await again.goto(resetLink);
  await again.getByLabel("New password", { exact: true }).fill("viewer reset pw 888");
  await again.getByRole("button", { name: "Set password and authenticator", exact: true }).click();
  await expect(again.getByRole("alert")).toHaveText("reset link invalid or expired");
  await again.close();
});

test("email sign-in link: the owner gets a one-time link, signs in with it and the code, and is told", async ({ page, baseURL }) => {
  await asClient(page, "203.0.113.4");
  await page.goto("/");
  const before = mails().length;
  await page.getByRole("button", { name: "Email me a sign-in link", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Email me a sign-in link", exact: true })).toBeVisible();
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  const answer = page.waitForResponse((r) => r.url().endsWith("/api/auth/email-link"));
  await page.getByRole("button", { name: "Send the link", exact: true }).click();
  const r = await answer;
  ownerAnswer = { status: r.status(), body: await r.text() };
  await expect(page.getByRole("status")).toHaveText(NEUTRAL);

  const mail = await mailAfter(before, LINK_SUBJECT);
  expect(mail.to).toBe(OWNER.email);
  expect(mail.body).toContain("works once and expires in 15 minutes");
  const link = signInLink(mail, baseURL!);
  const token = link.split("#signin=")[1];

  await openFromMail(page, link);
  await expect(page.getByRole("heading", { name: "Finish signing in", exact: true })).toBeVisible();
  expect(new URL(page.url()).hash).toBe("");                                   // the token leaves the address bar
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);   // the link replaces the password only
  await page.getByLabel("Authenticator code", { exact: true }).fill(clock.fresh(ownerSecret));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
  await expectRoleNotEmail(page, "Owner", OWNER.email);

  const notice = await mailAfter(before, NOTICE_SUBJECT);                      // ADR 0013: every such sign-in is announced
  expect(notice.to).toBe(OWNER.email);
  expect(notice.body).toContain("203.0.x.x");                                  // a coarse network hint only
  expect(notice.body).not.toContain("203.0.113.4");
  expect(notice.body).not.toContain(token);

  // wait for the sign-out to finish: reopening while its request is in flight cuts it off and leaves the token in the
  // tab's session storage (issue #87), so the reopened page would be a dashboard, not the sign-in form
  const loggedOut = page.waitForResponse((r) => r.url().endsWith("/api/auth/logout"));
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await loggedOut;
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await openFromMail(page, link);                                              // a used link is spent
  await page.getByLabel("Authenticator code", { exact: true }).fill(clock.fresh(ownerSecret));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "sign-in link invalid or expired, or wrong authenticator code. Ask for a new link below.");
  await expect(page.getByRole("heading", { name: "Email me a sign-in link", exact: true })).toBeVisible();
});

test("sign-in code: the owner signs in with the password and a code sent to Telegram; user management then needs the authenticator", async ({ page }) => {
  await asClient(page, "203.0.113.8");
  await page.goto("/");
  await expect(page.getByRole("radio", { name: "Send me a code", exact: true })).toBeChecked();
  await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
  await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
  const before = mails().length;
  await page.getByRole("button", { name: "Send me a code", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Enter the code we sent", exact: true })).toBeVisible();
  await expect(page.getByText(/a 6-digit code is on its way to your goldbot Telegram chat\. It works once, for 10 minutes/)).toBeVisible();
  await expect(page.getByText(/^Resend code in [01]:\d\d$/)).toBeVisible();
  await page.setViewportSize({ width: 360, height: 800 });                    // the code screen fits a phone too
  await fitsThePhone(page);
  await page.setViewportSize({ width: 1280, height: 720 });

  const mail = await mailAfter(before, CODE_SUBJECT);
  expect(mail.to).toBe("telegram");                                   // the fake code channel: the message the bot would send
  const code = mail.body.split("\n")[0];
  expect(code).toMatch(/^\d{6}$/);                                             // alone on the first line
  expect(mail.subject).not.toContain(code);
  await page.getByLabel("Code", { exact: true }).fill(code);                   // signs in on the sixth digit
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
  await expectRoleNotEmail(page, "Owner", OWNER.email);
  const recent = page.getByRole("region", { name: "Sign-ins with a Telegram code, last 7 days" });   // condition 6: every one is listed
  await expect(recent).toContainText("203.0.x.x");
  await expect(recent).not.toContainText(code);
  const stored = await page.evaluate(() => ({ local: Object.keys(localStorage), all: JSON.stringify({ ...localStorage, ...sessionStorage }) }));
  expect(stored.local.filter((k) => k !== "signin.method")).toEqual([]);      // the browser keeps no code or password
  expect(stored.all).not.toContain(code);
  expect(stored.all).not.toContain(OWNER.password);

  // ADR 0028 ruling 2: this session was opened with an emailed code, so an invite also needs the authenticator
  await go(page, "Users");
  const stepup = page.getByLabel("Authenticator code (or a recovery code)", { exact: true });
  await expect(stepup).toBeVisible();
  await page.getByPlaceholder("email to invite").fill("approver@example.com");
  await page.getByRole("button", { name: "Create invite link", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "This action needs a current code from your authenticator app (or a recovery code), because you signed in with " +
    "a sign-in code. Type it above and try again.");
  await expect(page.locator("code.mono")).toHaveCount(0);                      // refused: no invite was made
  await stepup.fill(clock.fresh(ownerSecret));
  await page.getByRole("button", { name: "Create invite link", exact: true }).click();
  await expect(page.locator("code.mono")).toContainText("/#invite=");
  await expect(stepup).toHaveValue("");                                        // spent: the next action asks afresh

  const loggedOut = page.waitForResponse((r) => r.url().endsWith("/api/auth/logout"));
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await loggedOut;
});

test("unknown and non-owner addresses get the same neutral answer as the owner, and no email", async ({ page }) => {
  await asClient(page, "203.0.113.5");
  await page.goto("/");
  const before = mails().length;
  await page.getByRole("button", { name: "Email me a sign-in link", exact: true }).click();
  for (const who of [UNKNOWN, VIEWER.email]) {
    await page.getByLabel("Email", { exact: true }).fill(who);
    await page.getByRole("button", { name: "Send the link", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText(NEUTRAL);
  }

  // the API answers byte for byte as it did the owner's request (the previous test); each from its own client
  const ask = async (email: string, ip: string) => {
    const r = await page.request.post("/api/auth/email-link", { data: { email }, headers: { "cf-connecting-ip": ip } });
    return { status: r.status(), body: await r.text() };
  };
  expect(ownerAnswer).toEqual({ status: 200, body: JSON.stringify({ ok: true, detail: NEUTRAL }) });
  expect(await ask(UNKNOWN, "203.0.113.51")).toEqual(ownerAnswer);
  expect(await ask(VIEWER.email, "203.0.113.52")).toEqual(ownerAnswer);
  // no mail: these requests queue no send at all; the owner's link went out within a second in the previous test
  await page.waitForTimeout(1_500);
  expect(mails().slice(before)).toEqual([]);

  // forgot-password: an unknown account and a known one with a wrong code are refused alike
  const forgot = async (email: string, code: string, ip: string) => {
    const r = await page.request.post("/api/auth/password/forgot", {
      data: { email, totp: code, new_password: "a fresh password 99" }, headers: { "cf-connecting-ip": ip } });
    return { status: r.status(), body: await r.text() };
  };
  const nobody = await forgot(UNKNOWN, clock.wrong(viewerSecret), "203.0.113.54");
  expect(await forgot(VIEWER.email, clock.wrong(viewerSecret), "203.0.113.55")).toEqual(nobody);
  expect(nobody).toEqual({ status: 403, body: JSON.stringify({ detail: "invalid credentials" }) });
});

test("a session the server no longer knows sends the page back to the sign-in screen", async ({ page }) => {
  await asClient(page, "203.0.113.7");
  await page.addInitScript(() => sessionStorage.setItem("goldbot_token", "revoked-or-expired"));   // a stale tab
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("goldbot_token"))).toBeNull();
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true });

  test("sign-in screens fit a phone, and the owner signs in with an emailed link there", async ({ page, baseURL }) => {
    await asClient(page, "203.0.113.6");
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
    await fitsThePhone(page);
    await page.getByRole("button", { name: "Forgot password?", exact: true }).tap();
    await expect(page.getByRole("heading", { name: "Reset your password", exact: true })).toBeVisible();
    await fitsThePhone(page);
    await page.getByRole("button", { name: "Back to sign in", exact: true }).tap();

    const before = mails().length;
    await page.getByRole("button", { name: "Email me a sign-in link", exact: true }).tap();
    await fitsThePhone(page);
    await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
    await page.getByRole("button", { name: "Send the link", exact: true }).tap();
    await expect(page.getByRole("status")).toHaveText(NEUTRAL);
    await fitsThePhone(page);

    await openFromMail(page, signInLink(await mailAfter(before, LINK_SUBJECT), baseURL!));
    await expect(page.getByRole("heading", { name: "Finish signing in", exact: true })).toBeVisible();
    await fitsThePhone(page);
    const code = page.getByLabel("Authenticator code", { exact: true });
    await expect(code).toHaveAttribute("inputmode", "numeric");               // the digit keypad
    await code.fill(clock.fresh(ownerSecret));
    await page.getByRole("button", { name: "Sign in", exact: true }).tap();
    await expectRoleNotEmail(page, "Owner", OWNER.email);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
    // row 1 holds "goldbot", the role and Sign out: the role never wraps under the title
    const banner = page.getByRole("banner");
    const title = await banner.getByRole("heading", { name: "goldbot", exact: true }).boundingBox();
    const role = await banner.locator(".who").boundingBox();
    expect(title && role && Math.abs(title.y + title.height / 2 - (role.y + role.height / 2)) < 4).toBe(true);
    const signOut = await banner.getByRole("button", { name: "Sign out", exact: true }).boundingBox();
    expect(signOut && signOut.height >= 44).toBe(true);                       // a thumb-sized tap target
    expect(signOut && signOut.x + signOut.width <= 360).toBe(true);
  });
});
