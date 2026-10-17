import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "../lib/api";
import type { Me } from "../lib/api";
import { CodeSignInsBanner } from "./CodeSignInsBanner";

// ADR 0028 as amended 2026-10-17, security condition 6: after every sign-in the owner sees the Telegram-code sign-ins of
// the last 7 days (time, coarse network) with a one-tap "Sign out everywhere".
const ONE = { sign_ins: [{ at_utc: "2026-10-17 09:30 UTC", network: "203.0.x.x" }], channel: "telegram" as const };

function renderAs(signedInWith: Me["signed_in_with"], answer: typeof ONE | { sign_ins: never[]; channel: "telegram" } = ONE) {
  apiModule.setUser({ email: "owner@example.com", role: "owner", signed_in_with: signedInWith });
  vi.spyOn(apiModule.api, "codeSignIns").mockResolvedValue(answer);
  const onSignedOut = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><CodeSignInsBanner onSignedOut={onSignedOut} /></QueryClientProvider>);
  return onSignedOut;
}

describe("Telegram-code sign-ins banner", () => {
  afterEach(() => { vi.restoreAllMocks(); apiModule.setUser(null); apiModule.setToken(null); });

  it("lists each Telegram-code sign-in with its time and coarse network", async () => {
    renderAs("password_totp");
    expect(await screen.findByText("2026-10-17 09:30 UTC from 203.0.x.x")).toBeInTheDocument();
  });

  it("shows nothing when there were no Telegram-code sign-ins", async () => {
    renderAs("password_totp", { sign_ins: [], channel: "telegram" });
    await waitFor(() => expect(apiModule.api.codeSignIns).toHaveBeenCalled());
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("signs everyone out with one tap after a password and authenticator sign-in", async () => {
    const onSignedOut = renderAs("password_totp");
    const revoke = vi.spyOn(apiModule.api, "revokeSessions").mockResolvedValue({ ok: true, sessions: 2 });
    fireEvent.click(await screen.findByRole("button", { name: "Sign out everywhere" }));
    await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
    expect(revoke).toHaveBeenCalledWith("owner@example.com", "");
    expect(screen.queryByLabelText("Authenticator code to sign out everywhere")).not.toBeInTheDocument();
  });

  it("asks for the authenticator first after a code sign-in and sends it with the call", async () => {
    const onSignedOut = renderAs("email_code");
    const revoke = vi.spyOn(apiModule.api, "revokeSessions").mockResolvedValue({ ok: true, sessions: 1 });
    fireEvent.change(await screen.findByLabelText("Authenticator code to sign out everywhere"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));
    await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
    expect(revoke).toHaveBeenCalledWith("owner@example.com", "123456");
  });

  it("says so and stays signed in when the server refuses", async () => {
    const onSignedOut = renderAs("password_totp");
    vi.spyOn(apiModule.api, "revokeSessions").mockRejectedValue(new Error(apiModule.SERVER_SAYS.stepupRequired));
    fireEvent.click(await screen.findByRole("button", { name: "Sign out everywhere" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Type a current code from your authenticator app");
    expect(screen.getByLabelText("Authenticator code to sign out everywhere")).toBeInTheDocument();
    expect(onSignedOut).not.toHaveBeenCalled();
  });

  it("can be hidden for this visit", async () => {
    renderAs("password_totp");
    fireEvent.click(await screen.findByRole("button", { name: "Hide" }));
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });
});
