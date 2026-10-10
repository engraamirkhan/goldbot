import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("invite and reset links", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    history.replaceState(null, "", "/");
  });

  it("puts the token in the fragment, never the query string", async () => {
    const { linkUrl } = await import("./links");
    expect(linkUrl("invite", "a-b_c", "https://dash.example.com")).toBe("https://dash.example.com/#invite=a-b_c");
    expect(linkUrl("reset", "tok", "https://dash.example.com")).not.toContain("?");
  });

  it("reads the token from the fragment once and clears it from the URL", async () => {
    history.replaceState(null, "", "/#reset=tok-1");
    const { takeLinkTokens } = await import("./links");
    expect(takeLinkTokens()).toEqual({ invite: "", reset: "tok-1" });
    expect(location.hash).toBe("");
    expect(takeLinkTokens().reset).toBe("tok-1"); // StrictMode's second render sees the same value
  });

  it("ignores a token in the query string", async () => {
    history.replaceState(null, "", "/?invite=tok-2");
    const { takeLinkTokens } = await import("./links");
    expect(takeLinkTokens()).toEqual({ invite: "", reset: "" });
  });
});
