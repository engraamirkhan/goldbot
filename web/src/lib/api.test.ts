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

  it("drops the session on 401", async () => {
    const { api, setToken, hasToken } = await import("./api");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "login required" })));
    setToken("expired");
    await expect(api.accounts()).rejects.toThrow("login required");
    expect(hasToken()).toBe(false);
    expect(sessionStorage.getItem("goldbot_token")).toBeNull();
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
