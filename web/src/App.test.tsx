import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "./lib/api";
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
