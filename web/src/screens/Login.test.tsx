import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// links.ts reads the URL fragment once per module instance: each test gets fresh modules, with the URL set first.
async function setup(hash = "", emailLink = true, emailCode = false) {
  history.replaceState(null, "", `/${hash}`);
  vi.resetModules();
  const apiModule = await import("../lib/api");
  vi.spyOn(apiModule.api, "authState").mockResolvedValue({ needs_setup: false, users: 2, email_link: emailLink, code_sign_in: emailCode, code_channel: "mail" });
  const { Login } = await import("./Login");
  const onLogin = vi.fn();
  render(<Login onLogin={onLogin} />);
  await act(async () => {});                                  // the form waits for the server's sign-in options
  return { api: apiModule.api, onLogin, ApiError: apiModule.ApiError, SERVER_SAYS: apiModule.SERVER_SAYS };
}

function type(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

const SESSION = { token: "t", expires_in: 43200, role: "owner" as const, email: "o@x.io", signed_in_with: "password_totp" as const };

describe("Login", () => {
  beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
  afterEach(() => { vi.restoreAllMocks(); history.replaceState(null, "", "/"); });

  it("signs in with email, password and the authenticator code", async () => {
    const { api, onLogin } = await setup();
    const login = vi.spyOn(api, "login").mockResolvedValue(SESSION);
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    type("Email", "o@x.io");
    type("Password", "a long password here");
    type("Authenticator code", "123456");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Authenticator code")).toHaveAttribute("inputmode", "numeric");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("o@x.io", "a long password here", "123456"));
    expect(onLogin).toHaveBeenCalled();
  });

  it("resets a forgotten password with the authenticator code, and the recovery code for the owner", async () => {
    const { api } = await setup();
    const forgot = vi.spyOn(api, "forgotPassword").mockResolvedValue({ ok: true });
    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));
    expect(screen.getByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
    expect(screen.getByText(/Lost the authenticator too\? Ask the owner for a reset link/)).toBeInTheDocument();
    type("Email", "o@x.io");
    type("Authenticator code", "123456");
    type("New password", "a brand new password");
    type(/Recovery code/, "abcd-efgh-ijkl-mnop");
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
    await waitFor(() => expect(forgot).toHaveBeenCalledWith("o@x.io", "123456", "a brand new password", "abcd-efgh-ijkl-mnop"));
    expect(await screen.findByRole("status")).toHaveTextContent("Password changed");
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("emails the owner a sign-in link and shows the same answer whatever the address", async () => {
    const { api } = await setup();
    const ask = vi.spyOn(api, "emailLink").mockResolvedValue({ ok: true, detail: "If this address can use email sign-in, a link is on its way" });
    fireEvent.click(await screen.findByRole("button", { name: "Email me a sign-in link" }));
    expect(screen.getByRole("heading", { name: "Email me a sign-in link" })).toBeInTheDocument();
    expect(screen.getByText(/still need the code from your authenticator app/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).toBeNull();
    type("Email", "o@x.io");
    fireEvent.click(screen.getByRole("button", { name: "Send the link" }));
    await waitFor(() => expect(ask).toHaveBeenCalledWith("o@x.io"));
    expect(await screen.findByRole("status")).toHaveTextContent("If this address can use email sign-in, a link is on its way");
  });

  it("finishes an emailed sign-in link with the authenticator code and clears the token from the address bar", async () => {
    const { api, onLogin } = await setup("#signin=tok-1");
    expect(location.hash).toBe("");
    const redeem = vi.spyOn(api, "redeemEmailLink").mockResolvedValue(SESSION);
    expect(await screen.findByRole("heading", { name: "Finish signing in" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).toBeNull();
    type("Authenticator code", "654321");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(redeem).toHaveBeenCalledWith("tok-1", "654321"));
    expect(onLogin).toHaveBeenCalled();
  });

  it("after a failed sign-in link goes straight to asking for a new one (the old link is spent)", async () => {
    const { api } = await setup("#signin=tok-2");
    vi.spyOn(api, "redeemEmailLink").mockRejectedValue(new Error("sign-in link invalid or expired, or wrong authenticator code"));
    await screen.findByRole("heading", { name: "Finish signing in" });
    type("Authenticator code", "000000");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Email me a sign-in link" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("invalid or expired");
    expect(screen.getByRole("alert")).toHaveTextContent("Ask for a new link");
    expect(screen.getByRole("button", { name: "Send the link" })).toBeInTheDocument();
  });

  it("after a failed sign-in link goes back to sign in when the server cannot send links", async () => {
    const { api } = await setup("#signin=tok-3", false);
    vi.spyOn(api, "redeemEmailLink").mockRejectedValue(new Error("sign-in link invalid or expired, or wrong authenticator code"));
    await screen.findByRole("heading", { name: "Finish signing in" });
    type("Authenticator code", "000000");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("invalid or expired");
  });

  it("does not offer the email link when the server has no mail configured", async () => {
    await setup("", false);
    await waitFor(() => expect(screen.getByRole("button", { name: "Forgot password?" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Email me a sign-in link" })).toBeNull();
  });

  it("opens the owner's reset link on the set-new-password screen", async () => {
    await setup("#reset=r-1");
    expect(await screen.findByRole("heading", { name: "Set a new password" })).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).toBeNull();
  });
});

// BACKLOG 77, docs/ux/sign-in-email-code.md states A-H: password + a 6-digit code emailed to the owner. The server
// answers every refusal with the same words and never says how many tries are left, so the screen counts them.
const CHALLENGE = { challenge: "ch-1", expires_in_s: 600, resend_after_s: 60 };
const REFUSED = "that code did not work, or it has expired or was already used";
const OWNER = { email: "owner@example.com", password: "owner password 123" };
const NETWORK = "Could not reach goldbot. Check your connection and try again.";
/** What lib/api.ts throws for an HTTP refusal: the server's words and the status (ApiError's shape). */
const refused = (status: number, message: string) => Object.assign(new Error(message), { status });
const offline = () => vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
const answering = (status: number, detail: string) => vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
  new Response(JSON.stringify({ detail }), { status, headers: { "content-type": "application/json" } })));
type Api = Awaited<ReturnType<typeof setup>>["api"];

async function emailCodeScreen() {
  const ctx = await setup("", false, true);
  await screen.findByRole("radio", { name: "Email me a code" });
  return ctx;
}

async function requestCode(api: Api, answer = CHALLENGE) {
  const ask = vi.spyOn(api, "emailCode").mockResolvedValue(answer);
  type("Email", OWNER.email);
  type("Password", OWNER.password);
  fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
  await screen.findByRole("heading", { name: "Enter the code we emailed" });
  return ask;
}

async function wrongCode(api: Api, value = "111111") {
  const verify = vi.spyOn(api, "verifyEmailCode").mockRejectedValue(refused(403, REFUSED));
  type("Code", value);
  await waitFor(() => expect(verify).toHaveBeenCalled());
  await screen.findByRole("alert");
  verify.mockClear();
}

const tick = (ms: number) => act(async () => { vi.advanceTimersByTime(ms); });

describe("Login with an emailed code", () => {
  beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); history.replaceState(null, "", "/"); });

  it("A: offers the email code by default when the server can send one, with the authenticator field slot kept but hidden", async () => {
    await emailCodeScreen();
    expect(screen.getByRole("group", { name: "Second step" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Email me a code" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Authenticator app" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Email me a code" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
    expect(screen.getByLabelText("Authenticator code")).not.toBeVisible();     // reserved: choosing it moves nothing
    expect(screen.getByLabelText("Authenticator code")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Forgot password?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use a recovery code" })).toBeInTheDocument();
  });

  it("A: choosing the authenticator shows today's code field and Sign in, and is remembered as the only thing stored", async () => {
    const { api, onLogin } = await emailCodeScreen();
    fireEvent.click(screen.getByRole("radio", { name: "Authenticator app" }));
    expect(screen.getByLabelText("Authenticator code")).toBeVisible();
    expect(localStorage.getItem("signin.method")).toBe("authenticator");
    const login = vi.spyOn(api, "login").mockResolvedValue(SESSION);
    type("Email", OWNER.email);
    type("Password", OWNER.password);
    type("Authenticator code", "123456");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(login).toHaveBeenCalledWith(OWNER.email, OWNER.password, "123456"));
    expect(onLogin).toHaveBeenCalled();
    expect(Object.keys(localStorage)).toEqual(["signin.method"]);

    cleanup();
    await emailCodeScreen();                                                   // the next visit starts on the last choice
    expect(screen.getByRole("radio", { name: "Authenticator app" })).toBeChecked();
  });

  it("A: shows Sending... while the code is requested", async () => {
    const { api } = await emailCodeScreen();
    let answer: (v: typeof CHALLENGE) => void = () => {};
    vi.spyOn(api, "emailCode").mockReturnValue(new Promise((r) => { answer = r; }));
    type("Email", OWNER.email);
    type("Password", OWNER.password);
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    expect(await screen.findByRole("button", { name: "Sending..." })).toBeDisabled();
    expect(screen.getByLabelText("Email")).toHaveValue(OWNER.email);
    await act(async () => answer(CHALLENGE));
  });

  it("B: after asking, shows the masked address, the expiry in local time with UTC beside, one numeric field and the resend countdown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 9, 17, 14, 32, 0));
    const { api } = await emailCodeScreen();
    const ask = await requestCode(api);
    expect(ask).toHaveBeenCalledWith(OWNER.email, OWNER.password);
    expect(screen.getByText(/If the email and password are right, a 6-digit code is on its way to/)).toHaveTextContent(
      "If the email and password are right, a 6-digit code is on its way to o•••@e•••.com. It works once, for 10 minutes (until 14:42).");
    const utc = new Date(2026, 9, 17, 14, 42, 0);
    expect(screen.getByText("14:42")).toHaveAttribute("title", `${String(utc.getUTCHours()).padStart(2, "0")}:42 UTC`);
    const field = screen.getByLabelText("Code");
    expect(field).toHaveFocus();
    expect(field).toHaveAttribute("inputmode", "numeric");
    expect(field).toHaveAttribute("autocomplete", "one-time-code");
    expect(field).toHaveAttribute("pattern", "[0-9 ]*");
    expect(field).toHaveAttribute("maxlength", "7");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("Resend code in 1:00")).toBeInTheDocument();
    expect(screen.getByTestId("live")).toHaveTextContent("Code sent");
    expect(screen.getByTestId("live")).toHaveAttribute("aria-live", "polite");
    await tick(13_000);
    expect(screen.getByText("Resend code in 0:47")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Resend code/ })).toBeNull();   // plain text, not a dead button
    expect(screen.getByTestId("live")).toHaveTextContent("Code sent");          // the seconds are not announced
  });

  it("B/G: signs in on the sixth digit, accepts a pasted code with a space, and never on a partial code", async () => {
    const { api, onLogin } = await emailCodeScreen();
    await requestCode(api);
    const verify = vi.spyOn(api, "verifyEmailCode").mockResolvedValue({ ...SESSION, signed_in_with: "email_code" });
    type("Code", "48291");
    expect(verify).not.toHaveBeenCalled();
    type("Code", "482 913");
    await waitFor(() => expect(verify).toHaveBeenCalledWith("ch-1", "482913"));
    expect(onLogin).toHaveBeenCalled();
    const apiModule = await import("../lib/api");
    expect(apiModule.currentUser()?.signed_in_with).toBe("email_code");
    // the code and the password are never kept by the browser: at most the method choice is stored
    expect(Object.keys(localStorage).filter((k) => k !== "signin.method")).toEqual([]);
    const stored = JSON.stringify({ ...localStorage, ...sessionStorage });
    expect(stored).not.toContain("482913");
    expect(stored).not.toContain(OWNER.password);
  });

  it("B: shows Checking... while the code is checked", async () => {
    const { api } = await emailCodeScreen();
    await requestCode(api);
    vi.spyOn(api, "verifyEmailCode").mockReturnValue(new Promise(() => {}));
    type("Code", "482913");
    expect(await screen.findByRole("button", { name: "Checking..." })).toBeDisabled();
  });

  it("B: 'Use the authenticator app instead' keeps the email and password; 'Start again' clears the password", async () => {
    const { api } = await emailCodeScreen();
    await requestCode(api);
    fireEvent.click(screen.getByRole("button", { name: "Use the authenticator app instead" }));
    expect(screen.getByRole("radio", { name: "Authenticator app" })).toBeChecked();
    expect(screen.getByLabelText("Email")).toHaveValue(OWNER.email);
    expect(screen.getByLabelText("Password")).toHaveValue(OWNER.password);
    expect(screen.getByLabelText("Authenticator code")).toBeVisible();

    fireEvent.click(screen.getByRole("radio", { name: "Email me a code" }));
    await requestCode(api);
    fireEvent.click(screen.getByRole("button", { name: "Start again" }));
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Email me a code" })).toBeChecked();
    expect(screen.getByLabelText("Email")).toHaveValue(OWNER.email);
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("C: counts wrong codes in the browser, then says the code is spent and offers a new one at once", async () => {
    const { api } = await emailCodeScreen();
    await requestCode(api);
    await wrongCode(api);
    const field = screen.getByLabelText("Code");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "That code did not work. Check the newest email from goldbot. 2 tries left for this code.");
    expect(field).toHaveAttribute("aria-describedby", screen.getByRole("alert").id);
    expect(field).toHaveFocus();
    await wrongCode(api, "222222");
    expect(screen.getByRole("alert")).toHaveTextContent("1 try left for this code.");
    expect(screen.queryByRole("button", { name: "Resend code" })).toBeNull();  // still cooling down
    await wrongCode(api, "333333");
    expect(screen.getByRole("alert")).toHaveTextContent("That code can no longer be used. Send a new one.");
    expect(screen.getByRole("button", { name: "Resend code" })).toBeEnabled(); // at once, not after the cooldown
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();    // a 4th guess would only feed the lockout
    expect(screen.getByRole("button", { name: "Start again" })).toBeInTheDocument();
  });

  it("C: a resend of a spent code that the server still paces shows the wait instead of a button that does nothing", async () => {
    const { api } = await emailCodeScreen();
    await requestCode(api);
    for (const c of ["111111", "222222", "333333"]) await wrongCode(api, c);
    vi.spyOn(api, "resendEmailCode").mockResolvedValue({ ...CHALLENGE, resend_after_s: 50 });   // the same challenge
    fireEvent.click(screen.getByRole("button", { name: "Resend code" }));
    expect(await screen.findByText("Resend code in 0:50")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resend code" })).toBeNull();
  });

  it("D: when the 10 minutes pass, says the code expired, announces it once and offers a new code", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = await emailCodeScreen();
    await requestCode(api);
    await tick(540_000);
    expect(screen.getByTestId("live")).toHaveTextContent("1 minute left");
    await tick(61_000);
    expect(screen.getByRole("alert")).toHaveTextContent("This code has expired or was already used. Send a new code.");
    expect(screen.getByTestId("live")).toHaveTextContent("Code expired");
    expect(screen.getByRole("button", { name: "Resend code" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  });

  it("E: Resend becomes a button after 60 s; a new challenge starts the code again, the same one means wait", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = await emailCodeScreen();
    await requestCode(api);
    await wrongCode(api);
    await tick(60_000);
    const resend = vi.spyOn(api, "resendEmailCode").mockResolvedValue({ ...CHALLENGE, challenge: "ch-2" });
    fireEvent.click(screen.getByRole("button", { name: "Resend code" }));
    await waitFor(() => expect(resend).toHaveBeenCalledWith("ch-1"));
    expect(await screen.findByText("Resend code in 1:00")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();                              // a new code: three fresh tries
    expect(screen.getByLabelText("Code")).toHaveValue("");
    await tick(60_000);
    resend.mockResolvedValue({ ...CHALLENGE, challenge: "ch-2", resend_after_s: 40 });   // the server says: not yet
    fireEvent.click(screen.getByRole("button", { name: "Resend code" }));
    expect(await screen.findByText("Resend code in 0:40")).toBeInTheDocument();
    const verify = vi.spyOn(api, "verifyEmailCode").mockResolvedValue({ ...SESSION, signed_in_with: "email_code" });
    type("Code", "123456");
    await waitFor(() => expect(verify).toHaveBeenCalledWith("ch-2", "123456"));
  });

  it("E: at the hourly limit says when to try again and offers the authenticator", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 9, 17, 14, 32, 0));
    const { api } = await emailCodeScreen();
    await requestCode(api, { ...CHALLENGE, resend_after_s: 1980 });
    expect(screen.getByText(/You have asked for 3 codes in the last hour/)).toHaveTextContent(
      "You have asked for 3 codes in the last hour. Try again after 15:05, or use the authenticator app.");
    expect(screen.getByText("15:05")).toHaveAttribute("title", expect.stringMatching(/^\d\d:05 UTC$/));
    expect(screen.getByRole("button", { name: "Use the authenticator app instead" })).toBeInTheDocument();
  });

  it("E: after 2 minutes without a code, suggests spam, the password, or the authenticator", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = await emailCodeScreen();
    await requestCode(api);
    const hint = "No email? Check the spam folder, check the password you typed (we do not say whether it was right), or use the authenticator app.";
    expect(screen.queryByText(hint)).toBeNull();
    await tick(120_000);
    expect(screen.getByText(hint)).toBeInTheDocument();
  });

  it("F: while sign-in is locked, says until when for both ways in and disables the button", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 9, 17, 14, 57, 0));
    const { api } = await emailCodeScreen();
    vi.spyOn(api, "emailCode").mockRejectedValue(refused(403, "too many attempts; wait 15 minutes"));
    type("Email", OWNER.email);
    type("Password", "a wrong password 1");
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many attempts. Sign-in for this account is paused for 15 minutes (until 15:12). This applies to the " +
      "email code and the authenticator alike. If you did not cause it, someone may know your password: change it once " +
      "you are back in.");
    expect(screen.getByRole("button", { name: "Email me a code" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "Authenticator app" }));
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
    await tick(15 * 60_000);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  // review round 1 (ui-ux 1-3, code LOW 6-8): no raw error reaches the owner, and only a refusal is a wrong try
  it("asking for a code while offline says so in words, never the browser's text", async () => {
    await emailCodeScreen();
    offline();
    type("Email", OWNER.email);
    type("Password", OWNER.password);
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(NETWORK);
    expect(screen.getByRole("alert")).not.toHaveTextContent("Failed to fetch");
  });

  it("asking for a code when the server fails says so in plain words and offers the authenticator", async () => {
    await emailCodeScreen();
    answering(500, "Internal Server Error");
    type("Email", OWNER.email);
    type("Password", OWNER.password);
    fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not send a code just now. Try again in a minute, or use the authenticator app.");
  });

  it("checking the code while offline says so in words and does not count as a wrong try", async () => {
    const { api } = await emailCodeScreen();
    await requestCode(api);
    offline();
    type("Code", "482913");
    expect(await screen.findByRole("alert")).toHaveTextContent(NETWORK);
    expect(screen.queryByText(/tries left|try left/)).toBeNull();
  });

  it("a server failure while checking the code is not a wrong try", async () => {
    const { api } = await emailCodeScreen();
    await requestCode(api);
    answering(503, "Service Unavailable");
    type("Code", "482913");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not check the code just now. Try again in a minute.");
    expect(screen.queryByText(/tries left|try left/)).toBeNull();
  });

  it("a lockout while checking the code is not a wrong try: it returns to sign in and says until when", async () => {
    const { api } = await emailCodeScreen();
    await requestCode(api);
    vi.spyOn(api, "verifyEmailCode").mockRejectedValue(refused(403, "too many attempts; wait 15 minutes"));
    type("Code", "482913");
    expect(await screen.findByRole("alert")).toHaveTextContent(/^Too many attempts\. Sign-in for this account is paused/);
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Email me a code" })).toBeDisabled();
  });

  it("a failed resend says so in plain words", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = await emailCodeScreen();
    await requestCode(api);
    await tick(60_000);
    answering(500, "Internal Server Error");
    fireEvent.click(screen.getByRole("button", { name: "Resend code" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not send a new code just now. Try again in a minute, or use the authenticator app.");
    offline();
    fireEvent.click(screen.getByRole("button", { name: "Resend code" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(NETWORK));
  });

  it("Enter on a partial code asks for all 6 digits and spends no try", async () => {
    const { api } = await emailCodeScreen();
    await requestCode(api);
    const verify = vi.spyOn(api, "verifyEmailCode");
    type("Code", "4829");
    fireEvent.submit(screen.getByLabelText("Code").closest("form")!);
    expect(await screen.findByRole("alert")).toHaveTextContent("Type all 6 digits from the email.");
    expect(verify).not.toHaveBeenCalled();
  });

  it("a pasted code and Enter at once send the code once", async () => {
    const { api } = await emailCodeScreen();
    await requestCode(api);
    const verify = vi.spyOn(api, "verifyEmailCode").mockReturnValue(new Promise(() => {}));
    const field = screen.getByLabelText("Code");
    act(() => {
      fireEvent.change(field, { target: { value: "482913" } });          // auto-verify on the 6th digit
      fireEvent.submit(field.closest("form")!);                           // and Enter, before React re-renders
    });
    await waitFor(() => expect(verify).toHaveBeenCalledTimes(1));
  });

  it("the password field has a Show toggle that reveals and hides it", async () => {
    await emailCodeScreen();
    const pw = screen.getByLabelText("Password");
    expect(pw).toHaveAttribute("type", "password");
    // the name is its visible text (it says what pressing does), and no label lookup for "Password" can hit it
    expect(screen.getAllByLabelText(/password/i)).toEqual([pw]);
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(pw).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Hide" })).toHaveAttribute("aria-controls", pw.id);
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(pw).toHaveAttribute("type", "password");
  });

  it("waits for the server's sign-in options before showing the form, so the method choice never pushes it down", async () => {
    history.replaceState(null, "", "/");
    vi.resetModules();
    const { api } = await import("../lib/api");
    let answer: (s: { needs_setup: boolean; users: number; email_link: boolean; code_sign_in: boolean; code_channel: "telegram" | "mail" }) => void = () => {};
    vi.spyOn(api, "authState").mockReturnValue(new Promise((r) => { answer = r; }));
    const { Login } = await import("./Login");
    render(<Login onLogin={vi.fn()} />);
    expect(screen.queryByLabelText("Email")).toBeNull();
    expect(screen.getByText("goldbot")).toBeInTheDocument();
    await act(async () => answer({ needs_setup: false, users: 1, email_link: false, code_sign_in: true, code_channel: "mail" }));
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Email me a code" })).toBeChecked();
  });

  it("shows today's form when the sign-in options cannot be loaded", async () => {
    history.replaceState(null, "", "/");
    vi.resetModules();
    const { api } = await import("../lib/api");
    vi.spyOn(api, "authState").mockRejectedValue(new Error("down"));
    const { Login } = await import("./Login");
    render(<Login onLogin={vi.fn()} />);
    expect(await screen.findByLabelText("Authenticator code")).toBeVisible();
  });

  it("first run still enrols an authenticator and says why, then offers the email code once the owner exists", async () => {
    history.replaceState(null, "", "/");
    vi.resetModules();
    const { api } = await import("../lib/api");
    const state = vi.spyOn(api, "authState").mockResolvedValue({ needs_setup: true, users: 0, email_link: false, code_sign_in: false, code_channel: "mail" });
    vi.spyOn(api, "setup").mockResolvedValue({ totp_uri: "otpauth://totp/goldbot?secret=ABC&issuer=goldbot", recovery_codes: ["r-1"] });
    const { Login } = await import("./Login");
    render(<Login onLogin={vi.fn()} />);
    await screen.findByRole("heading", { name: "Create the owner account" });
    type("Setup code", "s");
    type("Email", OWNER.email);
    type("Password", OWNER.password);
    fireEvent.click(screen.getByRole("button", { name: "Create owner account" }));
    expect(await screen.findByText(/You will need it to re-arm after a halt/)).toHaveTextContent(
      "Add goldbot to your authenticator app. You will need it to re-arm after a halt, switch auto mode and manage " +
      "users. Day-to-day sign-in can use a code sent to your email once mail is set up.");
    state.mockResolvedValue({ needs_setup: false, users: 1, email_link: false, code_sign_in: true, code_channel: "mail" });
    fireEvent.click(screen.getByRole("button", { name: "Continue to sign in" }));
    expect(await screen.findByRole("radio", { name: "Email me a code" })).toBeChecked();
  });

  it("H: without mail on the server the screen is today's: no method choice, the authenticator code and Sign in", async () => {
    localStorage.setItem("signin.method", "email");
    await setup("", false, false);
    await waitFor(() => expect(screen.getByRole("button", { name: "Forgot password?" })).toBeInTheDocument());
    expect(screen.queryByRole("group", { name: "Second step" })).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.getByLabelText("Authenticator code")).toBeVisible();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});

// ADR 0028 as amended 2026-10-17: with the Telegram channel the code is "sent", to the goldbot chat; no email is named.
describe("Login with a Telegram code", () => {
  beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
  afterEach(() => { vi.restoreAllMocks(); history.replaceState(null, "", "/"); });

  it("offers Send me a code and says the code went to the goldbot Telegram chat, without naming the mailbox", async () => {
    history.replaceState(null, "", "/");
    vi.resetModules();
    const apiModule = await import("../lib/api");
    vi.spyOn(apiModule.api, "authState").mockResolvedValue({ needs_setup: false, users: 1, email_link: false, code_sign_in: true, code_channel: "telegram" });
    const { Login } = await import("./Login");
    render(<Login onLogin={vi.fn()} />);
    await screen.findByRole("radio", { name: "Send me a code" });
    vi.spyOn(apiModule.api, "emailCode").mockResolvedValue({ challenge: "c", expires_in_s: 600, resend_after_s: 60 });
    type("Email", "o@x.io");
    type("Password", "a long password here");
    fireEvent.click(screen.getByRole("button", { name: "Send me a code" }));
    await screen.findByRole("heading", { name: "Enter the code we sent" });
    expect(screen.getByText(/we sent a 6-digit code to your goldbot Telegram chat/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/emailed|mailbox|o•••@/i);
  });
});
