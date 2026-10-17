import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "./lib/api";
import { clearLinkTokens, takeLinkTokens } from "./lib/links";
import { App } from "./App";

// The header says who is signed in by role, never by address: screenshots of the dashboard get shared
// (docs/ux/header-role.md, BACKLOG 64). innerHTML is checked so title / aria-label / data-* are covered too.
// The local part "owner" equals the role word, so the identity check is the domain and the "@".
class SilentSocket { onopen = null; onmessage = null; send() {} close() {} }

function renderApp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><App /></QueryClientProvider>);
}

// jsdom also gives screen-level <header>s the banner role (a browser does not, inside <main>); the app's is first.
const appBanner = () => screen.getAllByRole("banner")[0];

function expectNoIdentity(el: HTMLElement, email: string) {
  const domain = email.split("@")[1];
  for (const s of [email, domain, "@"]) {
    expect(el.textContent).not.toContain(s);
    expect(el.innerHTML).not.toContain(s);
  }
}

describe("App header", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", SilentSocket);
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));   // screens stay loading; the header needs no request
    apiModule.setToken("session-token");
  });
  afterEach(() => { vi.unstubAllGlobals(); apiModule.setToken(null); });

  it("shows the owner as 'Owner' with 'Signed in as' for screen readers, and never the email", () => {
    apiModule.setUser({ email: "owner@example.com", role: "owner", signed_in_with: "password_totp" });
    renderApp();
    const banner = appBanner();
    expect(banner).toHaveTextContent("Owner");
    expect(banner).toHaveTextContent("Signed in as Owner");
    expectNoIdentity(banner, "owner@example.com");
  });

  it.each([["approver", "Approver"], ["viewer", "Viewer"]] as const)(
    "shows the %s role as '%s' and never their email",
    (role, label) => {
      apiModule.setUser({ email: "v@x.io", role, signed_in_with: "password_totp" });
      renderApp();
      const banner = appBanner();
      expect(banner).toHaveTextContent(`Signed in as ${label}`);
      expectNoIdentity(banner, "v@x.io");
    },
  );

  it("says 'Signed in' when the session record is missing, without throwing", () => {
    apiModule.setUser(null);
    renderApp();
    const banner = appBanner();
    expect(banner).toHaveTextContent("Signed in");
    expect(banner).not.toHaveTextContent("Signed in as");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("keeps the email out of the page title", () => {
    apiModule.setUser({ email: "owner@example.com", role: "owner", signed_in_with: "password_totp" });
    renderApp();
    expect(document.title).not.toContain("@");
  });
});

// A session the server no longer knows (signed out elsewhere, expired, revoked, or a sign-out whose request was cut off
// by a page change) must send the person back to the sign-in screen. It used to leave the dashboard up, every query
// answering 401, forever: the e2e flake of issue #87 (a signed-out page reopened on a link showed this, not the form).
describe("App when the session is gone server-side", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", SilentSocket);
    vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(
      url.startsWith("/api/auth/state")
        ? new Response(JSON.stringify({ needs_setup: false, email_link: false, email_code: false }), { status: 200, headers: { "content-type": "application/json" } })
        : new Response(JSON.stringify({ detail: "login required" }), { status: 401, headers: { "content-type": "application/json" } }))));
    apiModule.setToken("revoked-token");
    apiModule.setUser({ email: "owner@example.com", role: "owner", signed_in_with: "password_totp" });
  });
  afterEach(() => { vi.unstubAllGlobals(); apiModule.setToken(null); });

  it("shows the sign-in screen after the first 401", async () => {
    renderApp();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();      // up until the server answers
    await waitFor(() => expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });
});

// The page was opened on an emailed sign-in link; signing out in that same page load must show the plain sign-in form,
// not "Finish signing in" with the spent link again (issue #89: the e2e's heading check lost the race to authState).
describe("App after signing out of a page opened on a sign-in link", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", SilentSocket);
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });
    vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(
      url.startsWith("/api/auth/state") ? json({ needs_setup: false, email_link: true })
        : url.startsWith("/api/auth/logout") ? json({ ok: true }) : json({}, 200))));
    clearLinkTokens();
    history.replaceState(null, "", "/#signin=spent-link");
    takeLinkTokens();                                            // what the first Login mount does with the URL
    apiModule.setToken("session-token");
    apiModule.setUser({ email: "owner@example.com", role: "owner", signed_in_with: "email_link" });
  });
  afterEach(() => { vi.unstubAllGlobals(); apiModule.setToken(null); history.replaceState(null, "", "/"); });

  it("shows Sign in, and keeps showing it once the server's auth state has arrived", async () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument());
    await waitFor(() => expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some((c) => String(c[0]).startsWith("/api/auth/state"))).toBe(true));
    await new Promise((r) => setTimeout(r, 50));                 // let the state answer be applied
    expect(screen.queryByRole("heading", { name: "Finish signing in" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});
