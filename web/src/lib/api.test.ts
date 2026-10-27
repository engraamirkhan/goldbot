import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { secondsLeft } from "./useNow";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("api client", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("an /api path answered by the page itself (older server, SPA fallback) is a 404, not a JSON parse error", async () => {
    const { api, ApiError } = await import("./api");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<!doctype html>", { status: 200, headers: { "content-type": "text/html; charset=utf-8" } })));
    const err = await api.strategyBoard().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it("sends the bearer token and parses JSON", async () => {
    const { api, setToken } = await import("./api");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { mode: "propose", halted: false, pending: 0, supervisor: {} }));
    vi.stubGlobal("fetch", fetchMock);
    setToken("tok-1");
    const s = await api.status();
    expect(s.pending).toBe(0);
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/status");
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok-1");
  });

  it("builds the calendar and news query strings from the typed parameters", async () => {
    const { api } = await import("./api");
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(200, [])));
    vi.stubGlobal("fetch", fetchMock);
    await api.calendar({ days: 3, max_tier: 1 });
    await api.news({ hours: 12, min_relevance: 0.5 });
    await api.news();
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "/api/calendar?days=3&max_tier=1", "/api/news?hours=12&min_relevance=0.5", "/api/news",
    ]);
  });

  it("posts the email-code request, verify and resend to their routes, and the step-up code with the six owner actions", async () => {
    const { api } = await import("./api");
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(200, {})));
    vi.stubGlobal("fetch", fetchMock);
    await api.emailCode("o@x.io", "pw");
    await api.verifyEmailCode("ch", "123456");
    await api.resendEmailCode("ch");
    await api.invite("n@x.io", "viewer", "111111");
    await api.setRole("n@x.io", "approver", "222222");
    await api.disable("n@x.io", "333333");
    await api.enable("n@x.io", "444444");
    await api.revokeSessions("n@x.io", "555555");
    await api.resetLink("n@x.io", "666666");
    await api.disable("n@x.io");                       // no step-up after an authenticator sign-in
    const sent = fetchMock.mock.calls.map((c) => [c[0], JSON.parse(c[1].body)]);
    expect(sent).toEqual([
      ["/api/auth/email-code", { email: "o@x.io", password: "pw" }],
      ["/api/auth/email-code/verify", { challenge: "ch", code: "123456" }],
      ["/api/auth/email-code/resend", { challenge: "ch" }],
      ["/api/auth/invite", { email: "n@x.io", role: "viewer", totp: "111111" }],
      ["/api/users/role", { email: "n@x.io", role: "approver", totp: "222222" }],
      ["/api/users/disable", { email: "n@x.io", totp: "333333" }],
      ["/api/users/enable", { email: "n@x.io", totp: "444444" }],
      ["/api/users/revoke-sessions", { email: "n@x.io", totp: "555555" }],
      ["/api/auth/reset-link", { email: "n@x.io", totp: "666666" }],
      ["/api/users/disable", { email: "n@x.io", totp: "" }],
    ]);
  });

  it("says a network failure in words, never the browser's text, and keeps every HTTP status on the error", async () => {
    const { api, ApiError, NETWORK_TEXT } = await import("./api");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const offline = await api.status().catch((e: unknown) => e);
    expect(offline).toBeInstanceOf(ApiError);
    expect(offline).toMatchObject({ status: 0, message: "Could not reach goldbot. Check your connection and try again." });
    expect(NETWORK_TEXT).toBe("Could not reach goldbot. Check your connection and try again.");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(503, { detail: "upstream down" })));
    expect(await api.status().catch((e: unknown) => e)).toMatchObject({ status: 503, message: "upstream down" });
  });

  it("drops the session on 401", async () => {
    const { api, setToken, hasToken } = await import("./api");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "login required" })));
    setToken("expired");
    await expect(api.accounts()).rejects.toThrow("login required");
    expect(hasToken()).toBe(false);
    expect(sessionStorage.getItem("goldbot_token")).toBeNull();
  });

  it("a late 401 for an old token does not end the session that replaced it", async () => {
    const { api, setToken, hasToken, onSessionEnded } = await import("./api");
    const ended = vi.fn();
    onSessionEnded(ended);
    let answer: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise<Response>((res) => { answer = res; })));
    setToken("token-A");
    const poll = api.accounts();                       // in flight with token A
    setToken("token-B");                               // a fresh sign-in lands meanwhile
    answer(jsonResponse(401, { detail: "login required" }));
    await expect(poll).rejects.toThrow("login required");
    expect(hasToken()).toBe(true);
    expect(sessionStorage.getItem("goldbot_token")).toBe("token-B");
    expect(ended).not.toHaveBeenCalled();
  });

  it("a 401 for a request sent without a token (a failed sign-in) is not a lost session", async () => {
    const { api, setToken, onSessionEnded } = await import("./api");
    const ended = vi.fn();
    onSessionEnded(ended);
    setToken(null);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "invalid credentials" })));
    await expect(api.accounts()).rejects.toThrow("login required");
    expect(ended).not.toHaveBeenCalled();
  });

  it("tells the app when the live socket is closed with 4401 for the current token, not for an old one", async () => {
    const { liveSocket, setToken, onSessionEnded, hasToken } = await import("./api");
    const sockets: { onclose: ((e: { code: number }) => void) | null; send: () => void; close: () => void }[] = [];
    vi.stubGlobal("WebSocket", class { onclose = null; send() {} close() {} constructor() { sockets.push(this as never); } });
    const ended = vi.fn();
    onSessionEnded(ended);
    setToken("token-A");
    liveSocket(() => {});
    setToken("token-B");
    sockets[0].onclose?.({ code: 4401 });              // the old socket's session is gone; B is a new one
    expect(hasToken()).toBe(true);
    expect(ended).not.toHaveBeenCalled();
    liveSocket(() => {});
    sockets[1].onclose?.({ code: 1006 });              // a dropped connection is not a lost session
    expect(hasToken()).toBe(true);
    sockets[1].onclose?.({ code: 4401 });
    expect(hasToken()).toBe(false);
    expect(ended).toHaveBeenCalledTimes(1);
  });

  it("surfaces the server's detail message on errors", async () => {
    const { api } = await import("./api");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(400, { detail: "cannot demote the last owner" })));
    await expect(api.setRole("a@x.io", "viewer")).rejects.toThrow("cannot demote the last owner");
  });

  it("ranks roles owner > approver > viewer", async () => {
    const { hasRole, setUser } = await import("./api");
    expect(hasRole("viewer")).toBe(false);   // nobody signed in
    setUser({ email: "a@x.io", role: "approver", signed_in_with: "password_totp" });
    expect(hasRole("viewer")).toBe(true);
    expect(hasRole("approver")).toBe(true);
    expect(hasRole("owner")).toBe(false);
  });

  it("restores the session from sessionStorage on load", async () => {
    sessionStorage.setItem("goldbot_token", "persisted");
    sessionStorage.setItem("goldbot_me", JSON.stringify({ email: "o@x.io", role: "owner" }));
    const { hasToken, currentUser } = await import("./api");
    expect(hasToken()).toBe(true);
    expect(currentUser()?.role).toBe("owner");
  });
});

describe("secondsLeft", () => {
  it("counts down to the expiry and never goes negative", () => {
    const now = Date.parse("2026-10-01T12:00:00Z");
    expect(secondsLeft("2026-10-01T12:01:30Z", now)).toBe(90);
    expect(secondsLeft("2026-10-01T11:59:00Z", now)).toBe(0);
  });
});
