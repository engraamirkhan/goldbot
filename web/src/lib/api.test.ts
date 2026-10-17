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
    setUser({ email: "a@x.io", role: "approver" });
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
