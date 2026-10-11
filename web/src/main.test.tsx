import { afterEach, describe, expect, it, vi } from "vitest";

// A signed-in browser never renders the login screen, so the link token must be taken from the address bar at start.
vi.mock("./App", () => ({ App: () => null }));

describe("app start", () => {
  afterEach(() => { history.replaceState(null, "", "/"); document.body.innerHTML = ""; });

  it("clears a link token from the address bar even when a session already exists", async () => {
    vi.resetModules();
    sessionStorage.setItem("goldbot_token", "existing-session");
    document.body.innerHTML = '<div id="root"></div>';
    history.replaceState(null, "", "/#signin=tok-9");
    await import("./main");
    expect(location.hash).toBe("");
    const { takeLinkTokens } = await import("./lib/links");
    expect(takeLinkTokens().signin).toBe("tok-9");     // still there for the login screen when it is shown
  });
});
